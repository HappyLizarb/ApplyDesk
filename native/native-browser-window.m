#import "native-browser.h"
#import <Carbon/Carbon.h>
#include <poll.h>
#include <unistd.h>

@implementation ADNativeBrowserState
@end

// Errors describe the operation without including typed content or private filesystem values.
NSError *ADNativeError(NSString *message) {
    return [NSError errorWithDomain:@"ApplyDeskNativeBrowser" code:1 userInfo:@{NSLocalizedDescriptionKey:message}];
}

// Accessibility values are copied only for an already scoped element.
id ADNativeAttribute(AXUIElementRef element, CFStringRef name) {
    CFTypeRef value = NULL;
    if (!element || AXUIElementCopyAttributeValue(element, name, &value) != kAXErrorSuccess) return nil;
    return CFBridgingRelease(value);
}

// Both native accessibility and CoreGraphics use screen points with a top-left origin here.
CGRect ADNativeElementBounds(AXUIElementRef element) {
    id position = ADNativeAttribute(element, kAXPositionAttribute), size = ADNativeAttribute(element, kAXSizeAttribute);
    CGPoint point = CGPointZero;CGSize dimensions = CGSizeZero;
    if (position && CFGetTypeID((__bridge CFTypeRef)position) == AXValueGetTypeID()) AXValueGetValue((__bridge AXValueRef)position, kAXValueCGPointType, &point);
    if (size && CFGetTypeID((__bridge CFTypeRef)size) == AXValueGetTypeID()) AXValueGetValue((__bridge AXValueRef)size, kAXValueCGSizeType, &dimensions);
    return (CGRect){point, dimensions};
}

// Geometry is explicit JSON so the Python bridge never guesses Retina pixel coordinates.
NSDictionary *ADNativeRect(CGRect rect) {
    return @{@"x":@(rect.origin.x), @"y":@(rect.origin.y), @"width":@(rect.size.width), @"height":@(rect.size.height)};
}

// The real default-profile process is distinct from ApplyDesk's separate interface Chrome process.
NSRunningApplication *ADNativeDefaultChrome(void) {
    NSString *root = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/Google/Chrome/SingletonLock"];
    NSString *lock = [NSFileManager.defaultManager destinationOfSymbolicLinkAtPath:root error:nil];
    pid_t pid = (pid_t)[[lock componentsSeparatedByString:@"-"] lastObject].intValue;
    NSRunningApplication *app = pid > 0 ? [NSRunningApplication runningApplicationWithProcessIdentifier:pid] : nil;
    return [app.bundleIdentifier isEqual:@"com.google.Chrome"] && [app.bundleURL.path isEqual:@"/Applications/Google Chrome.app"] && !app.terminated ? app : nil;
}

// Window enumeration reads identity and bounds, never another window's page contents.
NSArray<NSDictionary *> *ADNativeChromeWindows(pid_t pid) {
    NSArray *all = CFBridgingRelease(CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID));
    NSMutableArray *windows = [NSMutableArray new];
    for (NSDictionary *window in all) {
        if ([window[(id)kCGWindowOwnerPID] intValue] != pid || [window[(id)kCGWindowLayer] intValue] != 0) continue;
        CGRect bounds = CGRectZero;
        CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)window[(id)kCGWindowBounds], &bounds);
        if (bounds.size.width > 100 && bounds.size.height > 100) [windows addObject:window];
    }
    return windows;
}

// Parent loss, Stop or any queued command during input revokes the current event sequence.
BOOL ADNativeCheckLease(ADNativeBrowserState *state, NSError **error) {
    struct pollfd input = {STDIN_FILENO, POLLIN | POLLHUP, 0};
    BOOL canceled = state.stopped || getppid() != state.parentPID;
    if (state.performingInput && poll(&input, 1, 0) > 0) canceled = YES;
    if (canceled) { state.stopped = YES;if (error) *error = ADNativeError(@"Native browser input stopped.");return NO; }
    return YES;
}

// A retained native window is reusable only while its process, window role and current CG geometry agree.
static BOOL matchingAXIdentity(pid_t expectedPID, CGRect expected, id object) {
    if (!object) return NO;
    AXUIElementRef element = (__bridge AXUIElementRef)object;pid_t pid = 0;
    if (AXUIElementGetPid(element,&pid)!=kAXErrorSuccess || pid!=expectedPID
        || ![ADNativeAttribute(element,kAXRoleAttribute) isEqual:@"AXWindow"]) return NO;
    CGRect rect = ADNativeElementBounds(element);
    return fabs(rect.origin.x-expected.origin.x)<2 && fabs(rect.origin.y-expected.origin.y)<2
        && fabs(rect.size.width-expected.size.width)<2 && fabs(rect.size.height-expected.size.height)<2;
}

// Unavailable native inventory is distinct from a valid empty list and cannot establish creation provenance.
static NSArray *nativeAXWindows(pid_t pid) {
    AXUIElementRef app = AXUIElementCreateApplication(pid);
    AXUIElementSetMessagingTimeout(app,0.4);
    id windows = ADNativeAttribute(app,kAXWindowsAttribute);
    CFRelease(app);
    return [windows isKindOfClass:NSArray.class] ? windows : nil;
}

// A proven pair never adopts another AX identity; only cold bindings use unique geometry matching.
static id matchingAXWindow(ADNativeBrowserState *state) {
    if (state.windowElementID==state.windowID && state.windowElement)
        return matchingAXIdentity(state.pid,state.bounds,state.windowElement) ? state.windowElement : nil;
    NSArray *windows = nativeAXWindows(state.pid);
    NSMutableArray *matches = [NSMutableArray new];
    for (id window in windows) if (matchingAXIdentity(state.pid,state.bounds,window)) [matches addObject:window];
    state.windowElementID = matches.count==1 ? state.windowID : kCGNullWindowID;
    return matches.count == 1 ? matches.firstObject : nil;
}

// Each transition sample rechecks the lease, Chrome process and exact visible CoreGraphics window.
static BOOL refreshOwnedBounds(ADNativeBrowserState *state, NSError **error) {
    if (!ADNativeCheckLease(state,error)) return NO;
    NSRunningApplication *app = ADNativeDefaultChrome();
    NSDictionary *owned = nil;
    if (app.processIdentifier == state.pid) for (NSDictionary *window in ADNativeChromeWindows(state.pid))
        if ([window[(id)kCGWindowNumber] unsignedIntValue] == state.windowID) owned = window;
    if (!owned) {
        if (error) *error = [NSError errorWithDomain:@"ApplyDeskNativeBrowser" code:2 userInfo:@{NSLocalizedDescriptionKey:@"The owned Chrome window was closed.",@"native_code":@"window_closed"}];return NO;
    }
    if (!AXIsProcessTrusted()) { if (error) *error = ADNativeError(@"Accessibility permission is unavailable.");return NO; }
    if (![owned[(id)kCGWindowIsOnscreen] boolValue]) { if (error) *error = ADNativeError(@"Make the owned Chrome window visible before continuing.");return NO; }
    CGRect bounds = CGRectZero;CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)owned[(id)kCGWindowBounds], &bounds);state.bounds = bounds;
    return YES;
}

// Opening a native sheet can move AX before CG updates; resample the same proven pair without rebinding it.
static BOOL settleWindowBinding(ADNativeBrowserState *state, NSError **error) {
    BOOL retained = state.windowElementID==state.windowID && state.windowElement;
    NSTimeInterval deadline = 0;
    do {
        if (!refreshOwnedBounds(state,error)) return NO;
        id matched = matchingAXWindow(state);
        if (!ADNativeCheckLease(state,error)) return NO;
        if (matched) { state.windowElement = matched;return YES; }
        if (!retained) break;
        if (!deadline) deadline = NSProcessInfo.processInfo.systemUptime+0.6;
        if (NSProcessInfo.processInfo.systemUptime>=deadline) break;
        usleep(20000);
    } while (NSProcessInfo.processInfo.systemUptime<deadline);
    if (error) *error = ADNativeError(@"The owned Chrome window is ambiguous or unavailable.");return NO;
}

// Query live accessibility focus so a synchronous helper cannot reuse a cached workspace activation.
static BOOL ownedWindowFocused(ADNativeBrowserState *state) {
    AXUIElementRef system = AXUIElementCreateSystemWide();
    id front = ADNativeAttribute(system,kAXFocusedApplicationAttribute);
    CFRelease(system);
    pid_t pid = 0;
    if (!front || AXUIElementGetPid((__bridge AXUIElementRef)front,&pid)!=kAXErrorSuccess || pid!=state.pid) return NO;
    id focused = ADNativeAttribute((__bridge AXUIElementRef)front,kAXFocusedWindowAttribute);
    return focused && (CFEqual((__bridge CFTypeRef)focused,(__bridge CFTypeRef)state.windowElement)
        || ADNativeSurfaceFocus(state,(__bridge AXUIElementRef)focused));
}

// Every event still requires the proven window pair and exact live foreground focus.
BOOL ADNativeValidateWindow(ADNativeBrowserState *state, BOOL requireFront, NSError **error) {
    if (!settleWindowBinding(state,error)) return NO;
    if (requireFront && !ownedWindowFocused(state)) {
        if (error) *error = ADNativeError(@"The owned Chrome window lost focus. Observe it again.");
        return NO;
    }
    return YES;
}

// Hit testing excludes covered/offscreen content and never returns another application's control.
static BOOL hitOwnedOnce(ADNativeBrowserState *state, CGPoint point, AXUIElementRef expected) {
    if (!ADNativePointInWorkspace(state,point)) return NO;
    AXUIElementRef system = AXUIElementCreateSystemWide(), hit = NULL;
    AXError result = AXUIElementCopyElementAtPosition(system,point.x,point.y,&hit);CFRelease(system);
    if (result != kAXErrorSuccess || !hit) return NO;
    pid_t pid = 0;AXUIElementGetPid(hit,&pid);
    BOOL belongs = NO, target = expected == NULL;id original = CFBridgingRelease(hit), current = original;
    for (NSUInteger depth=0; current && depth<40; depth++) {
        if (expected && CFEqual((__bridge CFTypeRef)current,expected)) target = YES;
        if (CFEqual((__bridge CFTypeRef)current,(__bridge CFTypeRef)state.windowElement)) { belongs = YES;break; }
        current = ADNativeAttribute((__bridge AXUIElementRef)current,kAXParentAttribute);
    }
    BOOL remoteSheet = NO, attached = ADNativeSurfaceHit(state,point,(__bridge AXUIElementRef)original,&remoteSheet);
    if (target && attached && (pid==state.pid || remoteSheet)) return YES;
    if (pid != state.pid) return NO;
    if (belongs && (target || ADNativeTabOverlayHit(state,point,expected,(__bridge AXUIElementRef)original))) return YES;
    return ADNativeFieldOverlayHit(state,point,expected,(__bridge AXUIElementRef)original);
}

// Chrome exposes offscreen text as one-point strips; failed hits there do not benefit from cache retries.
static BOOL thinStaticText(AXUIElementRef expected) {
    if (!expected || ![ADNativeAttribute(expected,kAXRoleAttribute) isEqual:@"AXStaticText"]) return NO;
    id value = ADNativeAttribute(expected,kAXSizeAttribute);CGSize size = CGSizeZero;
    if (!value || CFGetTypeID((__bridge CFTypeRef)value)!=AXValueGetTypeID()
        || !AXValueGetValue((__bridge AXValueRef)value,kAXValueCGSizeType,&size)) return NO;
    return isfinite(size.width) && isfinite(size.height) && size.width>0 && size.height>0
        && (size.width<=1 || size.height<=1);
}

// Chrome's first native hit can be approximate; repeat the same point briefly without widening ownership.
BOOL ADNativeHitOwned(ADNativeBrowserState *state, CGPoint point, AXUIElementRef expected) {
    if (!ADNativePointInWorkspace(state,point)) return NO;
    for (NSUInteger attempt=0; attempt<4; attempt++) {
        if (!ADNativeCheckLease(state,nil)) return NO;
        if (attempt) usleep(20000);
        if (!ADNativeCheckLease(state,nil)) return NO;
        if (hitOwnedOnce(state,point,expected)) return YES;
        if (!attempt && thinStaticText(expected)) return NO;
    }
    return NO;
}

// Raising uses the native window action; it never changes page values through accessibility.
BOOL ADNativeRaise(ADNativeBrowserState *state, NSError **error) {
    if (!ADNativeValidateWindow(state,NO,error)) return NO;
    if (ownedWindowFocused(state)) return YES;
    [ADNativeDefaultChrome() activateWithOptions:0];
    if (AXUIElementPerformAction((__bridge AXUIElementRef)state.windowElement,kAXRaiseAction) != kAXErrorSuccess) {
        if (error) *error = ADNativeError(@"The owned Chrome window could not be raised.");return NO;
    }
    NSTimeInterval deadline = NSProcessInfo.processInfo.systemUptime + 1.5;
    do {
        if (!ADNativeCheckLease(state,error)) return NO;
        if (ownedWindowFocused(state)) break;
        [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.03]];
    } while (NSProcessInfo.processInfo.systemUptime < deadline);
    return ADNativeValidateWindow(state,YES,error);
}

// Reattachment is private backend state, not a model-provided target window selection.
BOOL ADNativeAttach(ADNativeBrowserState *state, NSDictionary *params, NSError **error) {
    state.pid = [params[@"pid"] intValue];state.windowID = [params[@"window_id"] unsignedIntValue];
    state.stopped = NO;state.generation += 1;state.observation = nil;
    if (!state.pid || !state.windowID || !ADNativeValidateWindow(state,NO,error)) { state.stopped = YES;return NO; }
    return ADNativeRaise(state,error);
}

// Chrome can place a utility surface in front of a regular window; use a unique geometry match behind it.
static NSDictionary *windowBinding(pid_t pid, id element, NSArray *windows, BOOL preferFront) {
    NSDictionary *match = nil;
    BOOL first = YES;
    for (NSDictionary *window in windows) {
        if (![window[(id)kCGWindowIsOnscreen] boolValue]) continue;
        CGRect bounds = CGRectZero;
        if (CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)window[(id)kCGWindowBounds],&bounds)
            && matchingAXIdentity(pid,bounds,element)) {
            if (match) return nil;
            match = @{@"window_id":window[(id)kCGWindowNumber],@"bounds":[NSValue valueWithRect:bounds],@"element":element};
            if (first && preferFront) return match;
        }
        first = NO;
    }
    return match;
}

// When Chrome has no focused regular window, choose one visible standard window with unambiguous AX and CG bounds.
static NSDictionary *existingWindowBinding(pid_t pid, id focused, NSArray *windows, NSArray *axWindows) {
    if ([ADNativeAttribute((__bridge AXUIElementRef)focused,kAXSubroleAttribute) isEqual:@"AXStandardWindow"]) {
        NSDictionary *binding = windowBinding(pid,focused,windows,YES);
        if (binding) return binding;
    }
    for (NSDictionary *window in windows) {
        if (![window[(id)kCGWindowIsOnscreen] boolValue]) continue;
        CGRect bounds = CGRectZero;
        if (!CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)window[(id)kCGWindowBounds],&bounds)) continue;
        NSMutableArray *matches = [NSMutableArray new];
        for (id candidate in axWindows) if (matchingAXIdentity(pid,bounds,candidate) &&
            [ADNativeAttribute((__bridge AXUIElementRef)candidate,kAXSubroleAttribute) isEqual:@"AXStandardWindow"])
            [matches addObject:candidate];
        if (matches.count != 1) continue;
        NSDictionary *binding = windowBinding(pid,matches.firstObject,windows,NO);
        if ([binding[@"window_id"] isEqual:window[(id)kCGWindowNumber]]) return binding;
    }
    return nil;
}

// Rebind the same window after unminimizing or exiting full screen; never create a replacement.
NSDictionary *ADNativeSelectExistingWindow(ADNativeBrowserState *state, NSRunningApplication *app) {
    AXUIElementRef application = AXUIElementCreateApplication(state.pid);
    id focused = ADNativeAttribute(application,kAXFocusedWindowAttribute);
    CFRelease(application);
    NSArray *windows = nativeAXWindows(state.pid);
    NSDictionary *binding = existingWindowBinding(state.pid,focused,ADNativeChromeWindows(state.pid),windows);
    id candidate = binding[@"element"] ?: ADNativeExistingCandidate(state.pid,focused,windows);
    AXUIElementRef target = (__bridge AXUIElementRef)candidate;
    if (binding && ![ADNativeAttribute(target,CFSTR("AXFullScreen")) boolValue]) return binding;
    if (!ADNativeCheckLease(state,nil) || !ADNativePresentExisting(app,candidate)) return nil;
    NSTimeInterval deadline = NSProcessInfo.processInfo.systemUptime + 3;
    do {
        if (!ADNativeCheckLease(state,nil)) return nil;
        if (![ADNativeAttribute(target,CFSTR("AXFullScreen")) boolValue]) {
            binding = windowBinding(state.pid,candidate,ADNativeChromeWindows(state.pid),NO);
            if (binding) return binding;
        }
        [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
    } while (NSProcessInfo.processInfo.systemUptime < deadline);
    return nil;
}
