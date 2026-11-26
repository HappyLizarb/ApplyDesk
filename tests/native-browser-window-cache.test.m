#import "../native-browser-window.h"

static NSMutableDictionary *fixtures;
static NSArray *listedWindows;
static NSUInteger listReads, geometryReads, roleReads;

// Fake AX identities and attributes make the binding test independent of any user window.
AXUIElementRef AXUIElementCreateApplication(pid_t pid) {
    return (AXUIElementRef)CFRetain((__bridge CFTypeRef)@"application");
}
AXError AXUIElementSetMessagingTimeout(AXUIElementRef element, float timeout) {
    return kAXErrorSuccess;
}
AXError AXUIElementGetPid(AXUIElementRef element, pid_t *pid) {
    NSDictionary *row = fixtures[(__bridge id)element];
    if (!row || [row[@"expired"] boolValue]) return kAXErrorInvalidUIElement;
    *pid = [row[@"pid"] intValue];return kAXErrorSuccess;
}

// Count native reads while preserving the real position/size value types used by the production helper.
AXError AXUIElementCopyAttributeValue(AXUIElementRef element, CFStringRef name, CFTypeRef *value) {
    id identity = (__bridge id)element;*value = NULL;
    if ([identity isEqual:@"application"] && CFEqual(name,kAXWindowsAttribute)) {
        listReads += 1;*value = CFRetain((__bridge CFTypeRef)listedWindows);return kAXErrorSuccess;
    }
    NSDictionary *row = fixtures[identity];
    if (!row || [row[@"expired"] boolValue]) return kAXErrorInvalidUIElement;
    CGRect rect = [row[@"bounds"] rectValue];
    if (CFEqual(name,kAXRoleAttribute)) { roleReads += 1;*value = CFRetain((__bridge CFTypeRef)row[@"role"]); }
    else if (CFEqual(name,kAXPositionAttribute)) { geometryReads += 1;*value = AXValueCreate(kAXValueCGPointType,&rect.origin); }
    else if (CFEqual(name,kAXSizeAttribute)) { geometryReads += 1;*value = AXValueCreate(kAXValueCGSizeType,&rect.size); }
    return *value ? kAXErrorSuccess : kAXErrorAttributeUnsupported;
}

#include "../native-browser-window.m"

// Twenty distinct native-window identities reproduce the avoidable per-character enumeration load.
static ADNativeBrowserState *fixtureState(void) {
    ADNativeBrowserState *state = [ADNativeBrowserState new];state.pid = 42;state.windowID = 7;
    state.bounds = CGRectMake(300,30,800,900);fixtures = [NSMutableDictionary new];
    NSMutableArray *windows = [NSMutableArray new];
    for (NSUInteger index=0; index<20; index++) {
        NSString *identity = [NSString stringWithFormat:@"window-%lu",(unsigned long)index];
        CGRect rect = index==7 ? state.bounds : CGRectMake(index*10,50,700,800);
        fixtures[identity] = [@{@"pid":@42,@"role":@"AXWindow",@"bounds":[NSValue valueWithRect:rect]} mutableCopy];
        [windows addObject:identity];
    }
    listedWindows = windows;listReads = 0;geometryReads = 0;roleReads = 0;
    return state;
}

// Warm checks read only the retained window while matching the current CG geometry every time.
static void retainedIdentity(void) {
    ADNativeBrowserState *state = fixtureState();state.windowElement = matchingAXWindow(state);
    NSCAssert([state.windowElement isEqual:@"window-7"] && listReads==1,@"Cold binding requires a unique full scan");
    geometryReads = 0;roleReads = 0;
    for (NSUInteger index=0; index<200; index++) NSCAssert([matchingAXWindow(state) isEqual:@"window-7"],@"Retain exact native identity");
    NSCAssert(listReads==1 && geometryReads==400 && roleReads==200,@"200 warm checks avoid 200 twenty-window enumerations");
    state.bounds = CGRectMake(360,70,800,900);fixtures[@"window-7"][@"bounds"] = [NSValue valueWithRect:state.bounds];
    NSCAssert([matchingAXWindow(state) isEqual:@"window-7"] && listReads==1,@"A moved window can retain exact identity at its fresh CG bounds");
}

// A broken proven identity fails closed rather than transferring ownership to a same-position replacement.
static void invalidatedIdentity(void) {
    ADNativeBrowserState *state = fixtureState();state.windowElement = matchingAXWindow(state);
    fixtures[@"window-7"][@"expired"] = @YES;
    fixtures[@"replacement"] = [@{@"pid":@42,@"role":@"AXWindow",@"bounds":[NSValue valueWithRect:state.bounds]} mutableCopy];
    listedWindows = @[@"replacement"];
    NSCAssert(matchingAXWindow(state)==nil && listReads==1,@"Expired identity cannot be silently replaced");
    NSCAssert([state.windowElement isEqual:@"window-7"] && state.windowElementID==7,@"Failure preserves the original binding");
    state.windowElement = nil;state.windowElementID = 0;
    NSCAssert([matchingAXWindow(state) isEqual:@"replacement"] && listReads==2,@"A fresh cold attachment can uniquely bind");
    state.windowElement = @"replacement";
    fixtures[@"replacement"][@"pid"] = @99;
    NSCAssert(matchingAXWindow(state)==nil,@"Same geometry from another process is rejected");
    fixtures[@"replacement"][@"pid"] = @42;fixtures[@"replacement"][@"role"] = @"AXSheet";
    NSCAssert(matchingAXWindow(state)==nil,@"An attached sheet cannot replace parent identity");
}

// Cold/mismatched geometry stays ambiguous instead of selecting an arbitrary same-sized window.
static void ambiguousIdentity(void) {
    ADNativeBrowserState *state = fixtureState();
    state.windowElement = matchingAXWindow(state);
    fixtures[@"window-8"][@"bounds"] = [NSValue valueWithRect:state.bounds];
    NSCAssert([matchingAXWindow(state) isEqual:@"window-7"],@"Previously proven exact identity survives another window at the same geometry");
    state.windowID = 8;
    NSCAssert(matchingAXWindow(state)==nil,@"A different CG window cannot inherit the cached AX binding");
    state.windowElement = nil;
    NSCAssert(matchingAXWindow(state)==nil,@"Cold duplicate geometry remains ambiguous");
    state.windowElement = @"window-7";state.windowElementID = state.windowID;
    fixtures[@"window-7"][@"bounds"] = [NSValue valueWithRect:CGRectMake(0,0,100,100)];
    NSCAssert(matchingAXWindow(state)==nil,@"Changed retained geometry cannot transfer ownership to the other window");
    state.windowElement = nil;listedWindows = @[];
    NSCAssert(matchingAXWindow(state)==nil,@"No native match remains an explicit failure");
}

int main(void) {
    @autoreleasepool { retainedIdentity();invalidatedIdentity();ambiguousIdentity();puts("Native window cache: 200 warm checks used 400 geometry reads and zero additional window enumerations; invalid/ambiguous bindings rejected without browser access."); }
    return 0;
}
