#import "../native-browser-window.h"
#include <unistd.h>

static NSDictionary *nodes;
static NSString *front, *focused;
AXUIElementRef AXUIElementCreateSystemWide(void) {
    return (AXUIElementRef)CFRetain((__bridge CFTypeRef)@"system");
}
// Synthetic AX identities let the fixture check focus and geometry without accessing Chrome.
AXUIElementRef AXUIElementCreateApplication(pid_t pid) {
    return (AXUIElementRef)CFRetain((__bridge CFTypeRef)@"application");
}
AXError AXUIElementSetMessagingTimeout(AXUIElementRef element, float timeout) { return kAXErrorSuccess; }
AXError AXUIElementGetPid(AXUIElementRef element, pid_t *pid) {
    NSDictionary *node = nodes[(__bridge id)element];
    if (!node) return kAXErrorInvalidUIElement;
    *pid = [node[@"pid"] intValue];return kAXErrorSuccess;
}

// Real AX geometry value types exercise the production native-role/PID/bounds comparison.
AXError AXUIElementCopyAttributeValue(AXUIElementRef element, CFStringRef key, CFTypeRef *value) {
    *value = NULL;id identity = (__bridge id)element;
    if ([identity isEqual:@"system"] && CFEqual(key,kAXFocusedApplicationAttribute) && front)
        *value = CFRetain((__bridge CFTypeRef)front);
    else if ([identity isEqual:front] && CFEqual(key,kAXFocusedWindowAttribute) && focused)
        *value = CFRetain((__bridge CFTypeRef)focused);
    else {
        NSDictionary *node = nodes[identity];CGRect rect = [node[@"bounds"] rectValue];
        if (CFEqual(key,kAXRoleAttribute) && node) *value = CFRetain((__bridge CFTypeRef)node[@"role"]);
        else if (CFEqual(key,kAXSubroleAttribute) && node[@"subrole"]) *value = CFRetain((__bridge CFTypeRef)node[@"subrole"]);
        else if (CFEqual(key,kAXPositionAttribute) && node) *value = AXValueCreate(kAXValueCGPointType,&rect.origin);
        else if (CFEqual(key,kAXSizeAttribute) && node) *value = AXValueCreate(kAXValueCGSizeType,&rect.size);
    }
    return *value ? kAXErrorSuccess : kAXErrorAttributeUnsupported;
}

#include "../native-browser-window.m"

// Duplicate geometry is deliberate: the identities, not their identical rectangles, distinguish old and new.
static NSDictionary *cgWindow(NSUInteger identity, CGRect rect) {
    return @{(id)kCGWindowNumber:@(identity),(id)kCGWindowOwnerPID:@42,(id)kCGWindowLayer:@0,
        (id)kCGWindowIsOnscreen:@YES,(id)kCGWindowBounds:CFBridgingRelease(CGRectCreateDictionaryRepresentation(rect))};
}
// Foreground order identifies an existing window even when a background window has the same dimensions.
int main(void) {
    @autoreleasepool {
        CGRect rect = CGRectMake(264,34,839,954), different = CGRectMake(20,30,700,600);
        nodes = @{@"chrome":@{@"pid":@42},@"other":@{@"pid":@17},@"focus":@{@"pid":@42,@"role":@"AXWindow",@"subrole":@"AXStandardWindow",@"bounds":[NSValue valueWithRect:rect]},
                  @"sheet":@{@"pid":@42,@"role":@"AXSheet",@"bounds":[NSValue valueWithRect:rect]}};
        NSArray *windows = @[cgWindow(2,rect),cgWindow(1,rect)];
        NSCAssert([existingWindowBinding(42,@"focus",windows,@[@"focus"])[@"window_id"] isEqual:@2],@"Prefer the focused regular window");
        NSArray *partial = @[cgWindow(3,different),cgWindow(2,rect)];
        NSCAssert([existingWindowBinding(42,@"focus",partial,@[@"focus"])[@"window_id"] isEqual:@2],@"A front utility surface does not hide Chrome");
        NSCAssert([existingWindowBinding(42,nil,partial,@[@"focus"])[@"element"] isEqual:@"focus"],@"Retain one unique regular window when focus is absent");
        NSCAssert(!existingWindowBinding(42,nil,windows,@[@"focus"]),@"Unfocused identical CG bounds are ambiguous");
        NSCAssert(!existingWindowBinding(99,@"focus",windows,@[@"focus"]),@"Reject foreign process");
        NSCAssert(!existingWindowBinding(42,@"sheet",windows,@[@"sheet"]),@"Reject standalone sheet");
        NSCAssert(!existingWindowBinding(42,@"focus",@[],@[@"focus"]),@"No windows means pause");
        NSCAssert([ADNativeExistingCandidate(42,@"sheet",@[@"sheet",@"focus"]) isEqual:@"focus"],@"Only an existing standard window can be restored");
        NSCAssert(!ADNativeExistingCandidate(99,@"focus",@[@"focus"]),@"Never restore a different process");
        NSCAssert(!ADNativeExistingCandidate(42,nil,@[]),@"Never create a missing window");
        ADNativeBrowserState *state = [ADNativeBrowserState new];
        state.pid = 42; state.windowElement = @"focus";
        front = @"other"; focused = @"focus";
        NSCAssert(!ownedWindowFocused(state),@"Background Chrome never receives input");
        front = @"chrome";
        NSCAssert(ownedWindowFocused(state),@"New live focus becomes usable");
        focused = @"another-window";
        NSCAssert(!ownedWindowFocused(state),@"A different Chrome window invalidates the lease");
        puts("Existing window and changing live focus verified without UI input.");
    }
    return 0;
}
