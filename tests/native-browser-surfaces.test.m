#import "../native-browser-window.h"

static NSMutableDictionary *nodes;
static NSArray *windowRecords;

@implementation ADNativeBrowserState
@end

// Isolated native fixtures contain identity tokens and geometry only, never real app handles or values.
id ADNativeAttribute(AXUIElementRef element, CFStringRef name) {
    return nodes[(__bridge id)element][(__bridge NSString *)name];
}
CGRect ADNativeElementBounds(AXUIElementRef element) {
    return [nodes[(__bridge id)element][@"bounds"] rectValue];
}
BOOL ADNativeCheckLease(ADNativeBrowserState *state, NSError **error) {
    return !state.stopped;
}
NSError *ADNativeError(NSString *message) {
    return [NSError errorWithDomain:@"fixture" code:1 userInfo:@{NSLocalizedDescriptionKey:message}];
}

// The CG substitute preserves exact-ID filtering without enumerating or capturing any real window.
static CFArrayRef fixtureWindows(CGWindowListOption options, CGWindowID identity) {
    NSArray *rows = windowRecords;
    if (options==kCGWindowListOptionIncludingWindow)
        rows = [rows filteredArrayUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(NSDictionary *row, NSDictionary *bindings) {
            return [row[(id)kCGWindowNumber] unsignedIntValue]==identity;
        }]];
    return CFBridgingRetain(rows);
}

#define CGWindowListCopyWindowInfo fixtureWindows
#include "../native-browser-surfaces.m"
#undef CGWindowListCopyWindowInfo

// CG metadata uses the same serialization as the OS while all identities stay fixture-local.
static NSDictionary *windowRecord(NSUInteger identifier, pid_t pid, CGRect bounds) {
    return @{(id)kCGWindowNumber:@(identifier),(id)kCGWindowOwnerPID:@(pid),
        (id)kCGWindowIsOnscreen:@YES,(id)kCGWindowBounds:CFBridgingRelease(CGRectCreateDictionaryRepresentation(bounds))};
}

// Native child edges establish ownership even when a popup's upward parent is its separate AXWindow.
static ADNativeBrowserState *fixture(NSString *role, NSString *subrole, CGRect rect) {
    ADNativeBrowserState *state = [ADNativeBrowserState new];
    state.pid = 42;state.windowID = 1;state.bounds = CGRectMake(300,30,800,900);state.windowElement = @"owned";
    nodes = [@{@"owned":[@{@"AXRole":@"AXWindow",@"AXChildren":@[@"container"],@"bounds":[NSValue valueWithRect:state.bounds]} mutableCopy],
        @"container":[@{@"AXRole":@"AXGroup",@"AXChildren":@[@"dialog"]} mutableCopy],
        @"dialog":[@{@"AXRole":role,@"AXSubrole":subrole,@"AXChildren":@[@"target"],@"AXParent":@"popup-window",@"bounds":[NSValue valueWithRect:rect]} mutableCopy],
        @"target":[@{@"AXRole":@"AXButton",@"AXParent":@"dialog"} mutableCopy],
        @"popup-window":[@{@"AXRole":@"AXWindow",@"bounds":[NSValue valueWithRect:rect]} mutableCopy],
        @"unrelated":[@{@"AXRole":@"AXButton",@"AXParent":@"other-window"} mutableCopy]} mutableCopy];
    windowRecords = @[windowRecord(1,42,state.bounds),windowRecord(2,42,rect)];
    return state;
}

// Exact reachable popup identity qualifies; arbitrary overlapping or reparented surfaces do not.
static void popupOwnership(void) {
    ADNativeBrowserState *state = fixture(@"AXGroup",@"AXApplicationAlertDialog",CGRectMake(500,110,450,390));
    NSCAssert(ADNativeRefreshSurfaces(state,nil) && state.nativeSurfaces.count==1,@"Collect one proven popup");
    BOOL remote = YES;CGPoint point = CGPointMake(600,200);
    NSCAssert(ADNativeSurfaceHit(state,point,(__bridge AXUIElementRef)@"target",&remote) && !remote,@"Popup hit carries no remote-process authority");
    NSCAssert(ADNativeSurfaceFocus(state,(__bridge AXUIElementRef)@"popup-window"),@"Exact attached popup window focus is accepted");
    NSCAssert(!ADNativeSurfaceHit(state,point,(__bridge AXUIElementRef)@"unrelated",NULL),@"Overlapping unrelated overlay is rejected");
    nodes[@"container"][@"AXChildren"] = @[];
    NSCAssert(!ADNativeSurfaceValid(state,state.nativeSurfaces.firstObject),@"Removed downward attachment revokes authority");
}

// A wider attached sheet expands workspace coordinates without changing the retained parent identity.
static void sheetOwnership(void) {
    CGRect sheet = CGRectMake(0,280,1500,450);
    ADNativeBrowserState *state = fixture(@"AXSheet",@"",sheet);
    NSCAssert(ADNativeRefreshSurfaces(state,nil),@"Collect attached sheet");
    CGPoint outsideParent = CGPointMake(1400,650);BOOL remote = NO;
    NSCAssert(ADNativePointInWorkspace(state,outsideParent),@"Proven sheet permits its outside-parent controls");
    NSCAssert(ADNativeSurfaceHit(state,outsideParent,(__bridge AXUIElementRef)@"target",&remote) && remote,@"Only a proven sheet permits remote UI ancestry");
    NSCAssert(!ADNativePointInWorkspace(state,CGPointMake(1600,650)),@"Other screen points stay excluded");
    NSCAssert(CGRectEqualToRect(ADNativeWorkspaceBounds(state),CGRectMake(0,30,1500,900)),@"Coordinate union is explicit");
    NSCAssert(state.windowID==1 && CGRectEqualToRect(state.bounds,CGRectMake(300,30,800,900)),@"Parent CG identity stays unchanged");
    NSCAssert(ADNativeSurfaceFocus(state,(__bridge AXUIElementRef)@"dialog"),@"Focused attached sheet is owned");
    windowRecords = @[windowRecord(1,42,state.bounds),windowRecord(2,999,sheet)];
    NSCAssert(!ADNativeSurfaceHit(state,outsideParent,(__bridge AXUIElementRef)@"target",NULL),@"CG owner replacement is rejected");
}

// Ambiguous geometry, webpage lookalikes and cancellation cannot create a native surface.
static void rejectedCandidates(void) {
    CGRect rect = CGRectMake(500,110,450,390);
    ADNativeBrowserState *state = fixture(@"AXSheet",@"",rect);
    windowRecords = @[windowRecord(2,42,rect),windowRecord(3,42,rect)];
    NSCAssert(ADNativeRefreshSurfaces(state,nil) && !state.nativeSurfaces.count,@"Duplicate CG geometry is ambiguous");
    state = fixture(@"AXGroup",@"AXApplicationAlertDialog",rect);
    windowRecords = @[windowRecord(1,42,state.bounds)];
    NSCAssert(!ADNativeIsDialog((__bridge AXUIElementRef)@"dialog") && ADNativeRefreshSurfaces(state,nil)
        && !state.nativeSurfaces.count,@"A nonmodal banner within the browser is not a native sheet");
    nodes[@"dialog"][@"AXModal"] = @YES;
    NSCAssert(ADNativeIsDialog((__bridge AXUIElementRef)@"dialog"),@"A modal alert without separate CG bounds still blocks access");
    nodes[@"container"][@"AXRole"] = @"AXWebArea";
    NSCAssert(ADNativeRefreshSurfaces(state,nil) && !state.nativeSurfaces.count,@"Web dialogs never become native surfaces");
    state.stopped = YES;
    NSCAssert(!ADNativeRefreshSurfaces(state,nil),@"Stop invalidates discovery");
}

int main(void) {
    @autoreleasepool { popupOwnership();sheetOwnership();rejectedCandidates();puts("Native attached surface ownership, remote-sheet scope, geometry, reparenting and Stop checks passed without browser access."); }
    return 0;
}
