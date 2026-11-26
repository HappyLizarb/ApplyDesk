#import "native-browser-window.h"

// Chrome exposes a childless native hit surface beside its semantic tab group, over the same strip.
BOOL ADNativeTabOverlayMatches(NSDictionary *evidence) {
    if (![evidence[@"tab_role"] isEqual:@"AXRadioButton"] || ![evidence[@"tab_subrole"] isEqual:@"AXTabButton"]
        || ![evidence[@"group_role"] isEqual:@"AXTabGroup"] || ![evidence[@"hit_role"] isEqual:@"AXGroup"]
        || ![evidence[@"same_container"] boolValue] || ![evidence[@"owned_native_ancestry"] boolValue]
        || [evidence[@"hit_child_count"] unsignedIntegerValue]!=0) return NO;
    NSDictionary *tab = evidence[@"tab_bounds"], *group = evidence[@"group_bounds"], *hit = evidence[@"hit_bounds"];
    if (![group isEqual:hit]) return NO;
    CGRect tabRect = CGRectMake([tab[@"x"] doubleValue],[tab[@"y"] doubleValue],[tab[@"width"] doubleValue],[tab[@"height"] doubleValue]);
    CGRect groupRect = CGRectMake([group[@"x"] doubleValue],[group[@"y"] doubleValue],[group[@"width"] doubleValue],[group[@"height"] doubleValue]);
    CGPoint point = CGPointMake([evidence[@"x"] doubleValue],[evidence[@"y"] doubleValue]);
    return !CGRectIsEmpty(tabRect) && CGRectContainsRect(groupRect,tabRect) && CGRectContainsPoint(tabRect,point);
}

// Only a native group chain ending at this exact owned window can supply tab-strip evidence.
static BOOL ownedNativeAncestors(ADNativeBrowserState *state, id current) {
    for (NSUInteger depth=0; current && depth<40; depth++) {
        AXUIElementRef element = (__bridge AXUIElementRef)current;
        if (CFEqual(element,(__bridge CFTypeRef)state.windowElement)) return YES;
        if (![ADNativeAttribute(element,kAXRoleAttribute) isEqual:@"AXGroup"]) return NO;
        current = ADNativeAttribute(element,kAXParentAttribute);
    }
    return NO;
}

// This exception applies to native Chrome tabs only; ordinary webpage controls keep exact hit ancestry.
BOOL ADNativeTabOverlayHit(ADNativeBrowserState *state, CGPoint point, AXUIElementRef expected, AXUIElementRef hit) {
    if (!expected || !hit) return NO;
    NSString *role = ADNativeAttribute(expected,kAXRoleAttribute);
    if (![role isEqual:@"AXRadioButton"]) return NO;
    NSString *subrole = ADNativeAttribute(expected,kAXSubroleAttribute);
    if (![subrole isEqual:@"AXTabButton"]) return NO;
    id group = ADNativeAttribute(expected,kAXParentAttribute);
    id container = ADNativeAttribute((__bridge AXUIElementRef)group,kAXParentAttribute);
    id hitParent = ADNativeAttribute(hit,kAXParentAttribute);
    BOOL same = container && hitParent && CFEqual((__bridge CFTypeRef)container,(__bridge CFTypeRef)hitParent);
    NSArray *children = ADNativeAttribute(hit,kAXChildrenAttribute), *visible = ADNativeAttribute(hit,kAXVisibleChildrenAttribute);
    NSDictionary *evidence = @{@"tab_role":role,@"tab_subrole":subrole,@"group_role":ADNativeAttribute((__bridge AXUIElementRef)group,kAXRoleAttribute) ?: @"",
        @"hit_role":ADNativeAttribute(hit,kAXRoleAttribute) ?: @"",@"same_container":@(same),
        @"owned_native_ancestry":@(same && ownedNativeAncestors(state,container)),@"hit_child_count":@(MAX(children.count,visible.count)),
        @"tab_bounds":ADNativeRect(ADNativeElementBounds(expected)),@"group_bounds":ADNativeRect(ADNativeElementBounds((__bridge AXUIElementRef)group)),
        @"hit_bounds":ADNativeRect(ADNativeElementBounds(hit)),@"x":@(point.x),@"y":@(point.y)};
    return ADNativeTabOverlayMatches(evidence);
}
