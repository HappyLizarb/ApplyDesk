#import "native-browser-window.h"

// A native autocomplete surface can cover its focused toolbar field without covering the painted text.
BOOL ADNativeFieldOverlayMatches(NSDictionary *evidence) {
    if (![@[@"AXTextField",@"AXComboBox"] containsObject:evidence[@"field_role"]]
        || [evidence[@"secure"] boolValue] || ![evidence[@"focused"] boolValue] || ![evidence[@"app_focus"] boolValue]
        || ![evidence[@"native_toolbar_ancestry"] boolValue] || ![evidence[@"owned_overlay"] boolValue]
        || ![evidence[@"hit_role"] isEqual:@"AXGroup"] || [evidence[@"hit_child_count"] unsignedIntegerValue]!=0) return NO;
    NSDictionary *field = evidence[@"field_bounds"], *parent = evidence[@"parent_bounds"], *hit = evidence[@"hit_bounds"];
    if (![parent isEqual:hit]) return NO;
    CGRect fieldRect = CGRectMake([field[@"x"] doubleValue],[field[@"y"] doubleValue],[field[@"width"] doubleValue],[field[@"height"] doubleValue]);
    CGRect parentRect = CGRectMake([parent[@"x"] doubleValue],[parent[@"y"] doubleValue],[parent[@"width"] doubleValue],[parent[@"height"] doubleValue]);
    CGPoint point = CGPointMake([evidence[@"x"] doubleValue],[evidence[@"y"] doubleValue]);
    return !CGRectIsEmpty(fieldRect) && CGRectContainsRect(parentRect,fieldRect) && CGRectContainsPoint(fieldRect,point);
}

// Only native toolbar ancestry ending at the exact owned window qualifies; webpage fields never do.
static BOOL nativeToolbarAncestors(ADNativeBrowserState *state, id current) {
    BOOL toolbar = NO;
    for (NSUInteger depth=0; current && depth<40; depth++) {
        AXUIElementRef element = (__bridge AXUIElementRef)current;
        if (CFEqual(element,(__bridge CFTypeRef)state.windowElement)) return toolbar;
        NSString *role = ADNativeAttribute(element,kAXRoleAttribute);
        if (![@[@"AXGroup",@"AXToolbar"] containsObject:role]) return NO;
        toolbar |= [role isEqual:@"AXToolbar"];current = ADNativeAttribute(element,kAXParentAttribute);
    }
    return NO;
}

// Chrome projects its popup under owned-window children even when popup parent links end elsewhere.
static BOOL ownedNativeSurface(AXUIElementRef root, AXUIElementRef hit, CGPoint point, NSUInteger depth, NSUInteger *budget) {
    if (!root || !*budget || depth>15) return NO;
    *budget -= 1;
    NSString *role = ADNativeAttribute(root,kAXRoleAttribute);
    if (![@[@"AXWindow",@"AXGroup"] containsObject:role]) return NO;
    CGRect bounds = ADNativeElementBounds(root);
    if (!CGRectIsEmpty(bounds) && !CGRectContainsPoint(bounds,point)) return NO;
    if (CFEqual(root,hit)) return YES;
    NSArray *children = ADNativeAttribute(root,kAXChildrenAttribute);
    for (id child in children)
        if (ownedNativeSurface((__bridge AXUIElementRef)child,hit,point,depth+1,budget)) return YES;
    return NO;
}

// Exact native focus and owned child identity authorize the field hit; no label or value is consulted.
BOOL ADNativeFieldOverlayHit(ADNativeBrowserState *state, CGPoint point, AXUIElementRef expected, AXUIElementRef hit) {
    if (!expected || !hit) return NO;
    NSString *role = ADNativeAttribute(expected,kAXRoleAttribute);
    if (![@[@"AXTextField",@"AXComboBox"] containsObject:role]) return NO;
    id parent = ADNativeAttribute(expected,kAXParentAttribute);
    if (!nativeToolbarAncestors(state,parent)) return NO;
    AXUIElementRef app = AXUIElementCreateApplication(state.pid);
    id focus = ADNativeAttribute(app,kAXFocusedUIElementAttribute);CFRelease(app);
    NSArray *children = ADNativeAttribute(hit,kAXChildrenAttribute), *visible = ADNativeAttribute(hit,kAXVisibleChildrenAttribute);
    NSUInteger budget = 128;
    NSDictionary *evidence = @{@"field_role":role,@"secure":@([ADNativeAttribute(expected,kAXSubroleAttribute) isEqual:(__bridge NSString *)kAXSecureTextFieldSubrole]),
        @"focused":ADNativeAttribute(expected,kAXFocusedAttribute) ?: @NO,@"app_focus":@(focus && CFEqual((__bridge CFTypeRef)focus,expected)),
        @"native_toolbar_ancestry":@YES,@"owned_overlay":@(ownedNativeSurface((__bridge AXUIElementRef)state.windowElement,hit,point,0,&budget)),
        @"hit_role":ADNativeAttribute(hit,kAXRoleAttribute) ?: @"",@"hit_child_count":@(MAX(children.count,visible.count)),
        @"field_bounds":ADNativeRect(ADNativeElementBounds(expected)),@"parent_bounds":ADNativeRect(ADNativeElementBounds((__bridge AXUIElementRef)parent)),
        @"hit_bounds":ADNativeRect(ADNativeElementBounds(hit)),@"x":@(point.x),@"y":@(point.y)};
    return ADNativeFieldOverlayMatches(evidence);
}
