#import "native-browser-window.h"

// Choose a standard window of the signed-in Chrome process, preferring its existing focus.
id ADNativeExistingCandidate(pid_t pid, id focused, NSArray *windows) {
    NSArray *ordered = focused ? [@[focused] arrayByAddingObjectsFromArray:windows ?: @[]] : windows;
    for (id object in ordered) {
        AXUIElementRef candidate = (__bridge AXUIElementRef)object;
        pid_t owner = 0;
        if (AXUIElementGetPid(candidate,&owner) == kAXErrorSuccess && owner == pid
            && [ADNativeAttribute(candidate,kAXRoleAttribute) isEqual:@"AXWindow"]
            && [ADNativeAttribute(candidate,kAXSubroleAttribute) isEqual:@"AXStandardWindow"]) return object;
    }
    return nil;
}

// Chrome puts its full-screen toolbar in a separate surface. Restore the selected window for tab access.
BOOL ADNativePresentExisting(NSRunningApplication *app, id object) {
    if (!object || app.terminated) return NO;
    AXUIElementRef window = (__bridge AXUIElementRef)object;
    [app activateWithOptions:0];
    if ([ADNativeAttribute(window,kAXMinimizedAttribute) boolValue]
        && AXUIElementSetAttributeValue(window,kAXMinimizedAttribute,kCFBooleanFalse) != kAXErrorSuccess) return NO;
    if ([ADNativeAttribute(window,CFSTR("AXFullScreen")) boolValue]
        && AXUIElementSetAttributeValue(window,CFSTR("AXFullScreen"),kCFBooleanFalse) != kAXErrorSuccess) return NO;
    return AXUIElementPerformAction(window,kAXRaiseAction) == kAXErrorSuccess;
}

// Chrome also uses AXApplicationAlertDialog for nonmodal toolbar notices.
BOOL ADNativeIsDialog(AXUIElementRef element) {
    NSString *role = ADNativeAttribute(element,kAXRoleAttribute);
    NSString *subrole = ADNativeAttribute(element,kAXSubroleAttribute);
    return [@[@"AXSheet",@"AXDialog"] containsObject:role]
        || [subrole isEqual:@"AXDialog"]
        || ([subrole isEqual:@"AXApplicationAlertDialog"] && [ADNativeAttribute(element,kAXModalAttribute) boolValue]);
}

// Each candidate window must be on-screen and match the dialog's exact geometry uniquely.
static NSDictionary *surfaceWindow(ADNativeBrowserState *state, CGRect rect, NSArray *windows) {
    NSMutableArray *matches = [NSMutableArray new];
    for (NSDictionary *window in windows) {
        if ([window[(id)kCGWindowOwnerPID] intValue]!=state.pid || ![window[(id)kCGWindowIsOnscreen] boolValue]
            || [window[(id)kCGWindowNumber] unsignedIntValue]==state.windowID) continue;
        CGRect bounds = CGRectZero;
        CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)window[(id)kCGWindowBounds],&bounds);
        if (CGRectEqualToRect(bounds,rect)) [matches addObject:window];
    }
    return matches.count==1 ? matches.firstObject : nil;
}

// Traversal stops before webpage and file-list contents; only the owned window's native dialog graph is inspected.
static void collectSurfaces(ADNativeBrowserState *state, id object, NSArray *path, NSArray *windows, NSMutableArray *surfaces, NSUInteger *budget) {
    if (!*budget || path.count>25 || surfaces.count>=12) return;
    *budget -= 1;AXUIElementRef element = (__bridge AXUIElementRef)object;
    NSString *role = ADNativeAttribute(element,kAXRoleAttribute) ?: @"";
    if ([@[@"AXWebArea",@"AXOutline",@"AXTable",@"AXBrowser"] containsObject:role]) return;
    NSArray *currentPath = [path arrayByAddingObject:object];
    if (ADNativeIsDialog(element) || [ADNativeAttribute(element,kAXSubroleAttribute) isEqual:@"AXApplicationAlertDialog"]) {
        CGRect bounds = ADNativeElementBounds(element);
        NSDictionary *window = CGRectIsEmpty(bounds) ? nil : surfaceWindow(state,bounds,windows);
        if (window) [surfaces addObject:@{@"element":object,@"path":currentPath,@"bounds":[NSValue valueWithRect:bounds],
            @"window_id":window[(id)kCGWindowNumber],@"role":role,
            @"subrole":ADNativeAttribute(element,kAXSubroleAttribute) ?: @""}];
    }
    for (id child in ADNativeAttribute(element,kAXChildrenAttribute)) {
        if (!ADNativeCheckLease(state,nil)) break;
        collectSurfaces(state,child,currentPath,windows,surfaces,budget);
    }
}

// Refresh only at observation boundaries; event-time checks follow the short retained native identity path.
BOOL ADNativeRefreshSurfaces(ADNativeBrowserState *state, NSError **error) {
    NSArray *windows = CFBridgingRelease(CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly,kCGNullWindowID));
    NSMutableArray *surfaces = [NSMutableArray new];NSUInteger budget = 600;
    collectSurfaces(state,state.windowElement,@[],windows,surfaces,&budget);
    if (!ADNativeCheckLease(state,error)) return NO;
    if (!budget || surfaces.count>=12) {
        if (error) *error = ADNativeError(@"The attached native dialog layout could not be inspected completely.");return NO;
    }
    state.nativeSurfaces = surfaces;
    return YES;
}

// Removed/reparented dialogs, changed CG identity and unrelated same-sized windows never inherit authority.
BOOL ADNativeSurfaceValid(ADNativeBrowserState *state, NSDictionary *surface) {
    NSArray *path = surface[@"path"];
    if (path.count<2 || !CFEqual((__bridge CFTypeRef)path.firstObject,(__bridge CFTypeRef)state.windowElement)) return NO;
    for (NSUInteger index=1; index<path.count; index++) {
        NSArray *children = ADNativeAttribute((__bridge AXUIElementRef)path[index-1],kAXChildrenAttribute);
        BOOL found = NO;
        for (id child in children) if (CFEqual((__bridge CFTypeRef)child,(__bridge CFTypeRef)path[index])) { found = YES;break; }
        if (!found) return NO;
    }
    CGRect expected = [surface[@"bounds"] rectValue];
    if (!CGRectEqualToRect(expected,ADNativeElementBounds((__bridge AXUIElementRef)surface[@"element"]))) return NO;
    NSArray *windows = CFBridgingRelease(CGWindowListCopyWindowInfo(kCGWindowListOptionIncludingWindow,[surface[@"window_id"] unsignedIntValue]));
    NSDictionary *match = surfaceWindow(state,expected,windows);
    return [match[(id)kCGWindowNumber] isEqual:surface[@"window_id"]];
}

// An exact dialog identity is returned only while its retained native attachment still exists.
NSDictionary *ADNativeSurface(ADNativeBrowserState *state, AXUIElementRef element) {
    for (NSDictionary *surface in state.nativeSurfaces)
        if (element && CFEqual(element,(__bridge CFTypeRef)surface[@"element"]) && ADNativeSurfaceValid(state,surface)) return surface;
    return nil;
}

// A focused native sheet, its descendant, or the dialog's actual AX window may retain owned-window focus.
BOOL ADNativeSurfaceFocus(ADNativeBrowserState *state, AXUIElementRef focused) {
    if (!focused) return NO;
    for (NSDictionary *surface in state.nativeSurfaces) {
        if (!ADNativeSurfaceValid(state,surface)) continue;
        id current = (__bridge id)focused;
        for (NSUInteger depth=0; current && depth<25; depth++) {
            if (CFEqual((__bridge CFTypeRef)current,(__bridge CFTypeRef)surface[@"element"])) return YES;
            current = ADNativeAttribute((__bridge AXUIElementRef)current,kAXParentAttribute);
        }
        current = surface[@"element"];
        for (NSUInteger depth=0; current && depth<10; depth++) {
            AXUIElementRef item = (__bridge AXUIElementRef)current;
            if ([ADNativeAttribute(item,kAXRoleAttribute) isEqual:@"AXWindow"]) {
                if (CFEqual(item,focused) && CGRectEqualToRect(ADNativeElementBounds(item),[surface[@"bounds"] rectValue])) return YES;
                break;
            }
            current = ADNativeAttribute(item,kAXParentAttribute);
        }
    }
    return NO;
}

// Remote Open-panel views qualify only when their hit ancestry contains the exact attached native sheet.
BOOL ADNativeSurfaceHit(ADNativeBrowserState *state, CGPoint point, AXUIElementRef hit, BOOL *remoteSheet) {
    for (NSDictionary *surface in state.nativeSurfaces) {
        if (!CGRectContainsPoint([surface[@"bounds"] rectValue],point) || !ADNativeSurfaceValid(state,surface)) continue;
        id current = (__bridge id)hit;
        for (NSUInteger depth=0; current && depth<40; depth++) {
            if (CFEqual((__bridge CFTypeRef)current,(__bridge CFTypeRef)surface[@"element"])) {
                if (remoteSheet) *remoteSheet = [surface[@"role"] isEqual:@"AXSheet"];
                return YES;
            }
            current = ADNativeAttribute((__bridge AXUIElementRef)current,kAXParentAttribute);
        }
    }
    return NO;
}

// Interaction points can extend beyond the parent rectangle only through a proven attached surface.
BOOL ADNativePointInWorkspace(ADNativeBrowserState *state, CGPoint point) {
    if (CGRectContainsPoint(state.bounds,point)) return YES;
    for (NSDictionary *surface in state.nativeSurfaces)
        if (CGRectContainsPoint([surface[@"bounds"] rectValue],point) && ADNativeSurfaceValid(state,surface)) return YES;
    return NO;
}

// Observation and screenshot coordinates share one union while state.bounds remains the parent CG geometry.
CGRect ADNativeWorkspaceBounds(ADNativeBrowserState *state) {
    CGRect bounds = state.bounds;
    for (NSDictionary *surface in state.nativeSurfaces)
        if (ADNativeSurfaceValid(state,surface)) bounds = CGRectUnion(bounds,[surface[@"bounds"] rectValue]);
    return bounds;
}

// Acquire an existing regular Chrome window on explicit open.
BOOL ADNativeUseExisting(ADNativeBrowserState *state, NSError **error) {
    NSRunningApplication *app = ADNativeDefaultChrome();
    if (!app || !AXIsProcessTrusted() || !CGPreflightPostEventAccess()) {
        if (error) *error = ADNativeError(@"Open ordinary Chrome and enable Accessibility/input permission.");
        return NO;
    }
    state.stopped = NO;
    state.generation += 1;
    if (state.windowID) return ADNativeRaise(state,error);
    state.pid = app.processIdentifier;
    NSDictionary *binding = ADNativeSelectExistingWindow(state,app);
    if (!binding) {
        state.stopped = YES;
        if (error) *error = ADNativeError(@"Bring an existing regular Chrome window forward, then retry the application.");
        return NO;
    }
    state.windowID = [binding[@"window_id"] unsignedIntValue];
    state.windowElementID = state.windowID;
    state.windowElement = binding[@"element"];
    state.bounds = [binding[@"bounds"] rectValue];
    return ADNativeRaise(state,error);
}
