#import "native-browser-numeric.h"
#import "native-browser-window.h"

// Only the observed single-child numeric composite qualifies; ordinary hidden fields cannot borrow geometry.
BOOL ADNativeNumericWrapperMatches(NSDictionary *evidence, CGRect nativeBounds, CGRect wrapperBounds, CGRect clip) {
    return [evidence[@"role"] isEqual:@"AXIncrementor"] && [evidence[@"wrapper_role"] isEqual:@"AXGroup"]
        && ![evidence[@"secure"] boolValue] && [evidence[@"enabled"] boolValue] && [evidence[@"value_settable"] boolValue]
        && [evidence[@"unique_child"] boolValue] && [evidence[@"exact_wrapper_hit"] boolValue] && [evidence[@"owned_wrapper"] boolValue]
        && CGRectIsEmpty(nativeBounds) && !CGRectIsEmpty(wrapperBounds) && CGRectContainsRect(clip,wrapperBounds)
        && CGRectContainsPoint(wrapperBounds,nativeBounds.origin);
}

// Recompute native parent identity, visibility and exact hit evidence each time the projected control is used.
NSDictionary *ADNativeNumericWrapper(ADNativeBrowserState *state, id object, CGRect clip) {
    if (!object) return nil;
    AXUIElementRef element = (__bridge AXUIElementRef)object;
    NSString *role = ADNativeAttribute(element,kAXRoleAttribute) ?: @"";
    CGRect nativeBounds = ADNativeElementBounds(element);
    if (![role isEqual:@"AXIncrementor"] || !CGRectIsEmpty(nativeBounds)) return nil;
    id parent = ADNativeAttribute(element,kAXParentAttribute);if (!parent) return nil;
    AXUIElementRef wrapper = (__bridge AXUIElementRef)parent;
    NSArray *children = ADNativeAttribute(wrapper,kAXChildrenAttribute) ?: @[];
    CGRect bounds = ADNativeElementBounds(wrapper);Boolean settable = false;
    AXUIElementIsAttributeSettable(element,kAXValueAttribute,&settable);
    BOOL unique = children.count==1 && CFEqual((__bridge CFTypeRef)children[0],element);
    if (!unique || CGRectIsEmpty(bounds) || !CGRectContainsRect(clip,bounds)) return nil;
    CGPoint point = CGPointMake(CGRectGetMidX(bounds),CGRectGetMidY(bounds));
    BOOL owned = ADNativeHitOwned(state,point,wrapper);
    AXUIElementRef system = AXUIElementCreateSystemWide(), hit = NULL;
    AXError status = AXUIElementCopyElementAtPosition(system,point.x,point.y,&hit);CFRelease(system);
    BOOL exact = status==kAXErrorSuccess && hit && CFEqual(hit,wrapper);if (hit) CFRelease(hit);
    NSDictionary *evidence = @{@"role":role,@"wrapper_role":ADNativeAttribute(wrapper,kAXRoleAttribute) ?: @"",
        @"secure":@([ADNativeAttribute(element,kAXSubroleAttribute) isEqual:@"AXSecureTextField"]),
        @"enabled":ADNativeAttribute(element,kAXEnabledAttribute) ?: @NO,@"value_settable":@(settable),
        @"unique_child":@(unique),@"exact_wrapper_hit":@(exact),@"owned_wrapper":@(owned)};
    return ADNativeNumericWrapperMatches(evidence,nativeBounds,bounds,clip)
        ? @{@"element":parent,@"bounds":[NSValue valueWithRect:bounds]} : nil;
}
