#import "native-browser.h"
#import <CommonCrypto/CommonDigest.h>

// AX identities are stable while Chrome keeps the same control; closed/replaced controls get new IDs.
static NSString *elementIdentity(ADNativeBrowserState *state, id element) {
    if (!state.stableElements) state.stableElements = [NSMutableDictionary new];
    for (NSString *identifier in state.stableElements)
        if (CFEqual((__bridge CFTypeRef)state.stableElements[identifier],(__bridge CFTypeRef)element)) {
            state.elements[identifier] = element;return identifier;
        }
    NSString *identifier = [NSString stringWithFormat:@"n%lu",(unsigned long)++state.nextElement];
    state.stableElements[identifier] = element;state.elements[identifier] = element;
    return identifier;
}

// Only strings/numbers used by native visible controls are exported, never arbitrary AX objects.
static id boundedValue(id value) {
    if ([value isKindOfClass:NSString.class]) return [value length]>1500 ? [value substringToIndex:1500] : value;
    if ([value isKindOfClass:NSNumber.class]) return value;
    return NSNull.null;
}

// Native password widgets can expose a nonsecure combo wrapper around the actual secure text control.
static BOOL secureControl(AXUIElementRef element, NSString *role, NSString *subrole) {
    if ([subrole isEqual:(__bridge NSString *)kAXSecureTextFieldSubrole]) return YES;
    if (![role isEqual:@"AXComboBox"]) return NO;
    for (id child in ADNativeAttribute(element,kAXChildrenAttribute))
        if ([ADNativeAttribute((__bridge AXUIElementRef)child,kAXSubroleAttribute) isEqual:(__bridge NSString *)kAXSecureTextFieldSubrole]) return YES;
    return NO;
}

// Chrome may provide an empty title alongside its useful visible accessibility description.
NSString *ADNativeControlLabel(id title, id description) {
    for (id candidate in @[title ?: NSNull.null,description ?: NSNull.null])
        if ([candidate isKindOfClass:NSString.class] && [candidate length]) return candidate;
    return @"";
}

// Native URL metadata is preserved verbatim; missing schemes and embedded credentials are never inferred.
NSString *ADNativeHTTPURL(id value) {
    NSString *text = [value isKindOfClass:NSURL.class] ? [value absoluteString] : [value isKindOfClass:NSString.class] ? value : nil;
    NSURLComponents *url = text ? [NSURLComponents componentsWithString:text] : nil;
    if (![@[@"http",@"https"] containsObject:url.scheme.lowercaseString] || !url.host.length || url.user || url.password) return nil;
    return text;
}

// Partially clipped text is read only through the OS-provided visible character range.
static id visibleValue(AXUIElementRef element, BOOL complete, BOOL secure) {
    if (secure) return NSNull.null;
    if (complete) return boundedValue(ADNativeAttribute(element,kAXValueAttribute));
    id range = ADNativeAttribute(element,kAXVisibleCharacterRangeAttribute);
    if (!range || CFGetTypeID((__bridge CFTypeRef)range) != AXValueGetTypeID()) return NSNull.null;
    CFRange visibleRange;
    if (!AXValueGetValue((__bridge AXValueRef)range,kAXValueCFRangeType,&visibleRange)
        || visibleRange.location<0 || visibleRange.length<=0) return NSNull.null;
    CFTypeRef text = NULL;
    if (AXUIElementCopyParameterizedAttributeValue(element,kAXStringForRangeParameterizedAttribute,(__bridge CFTypeRef)range,&text) != kAXErrorSuccess) return NSNull.null;
    id value = CFBridgingRelease(text);
    return [value isKindOfClass:NSString.class] && [value length] ? boundedValue(value) : NSNull.null;
}

// Hit-testable native semantics describe visible controls, including Chrome's tab strip and address field.
static NSMutableDictionary *controlRecord(ADNativeBrowserState *state, id object, CGRect clip, NSString *dialogID) {
    AXUIElementRef element = (__bridge AXUIElementRef)object;
    CGRect full = ADNativeElementBounds(element);
    NSDictionary *numeric = CGRectIsEmpty(full) ? ADNativeNumericWrapper(state,object,clip) : nil;
    if (numeric) full = [numeric[@"bounds"] rectValue];
    CGRect shown = CGRectIntersection(full,clip);
    CGPoint center = CGPointMake(CGRectGetMidX(shown),CGRectGetMidY(shown));
    AXUIElementRef hitTarget = numeric ? (__bridge AXUIElementRef)numeric[@"element"] : element;
    if (CGRectIsEmpty(shown) || !ADNativeHitOwned(state,center,hitTarget)) return nil;
    NSString *role = ADNativeAttribute(element,kAXRoleAttribute) ?: @"", *subrole = ADNativeAttribute(element,kAXSubroleAttribute) ?: @"";
    BOOL secure = secureControl(element,role,subrole);
    BOOL complete = CGRectContainsRect(clip,full);
    id value = visibleValue(element,complete,secure);
    NSString *label = complete ? ADNativeControlLabel(ADNativeAttribute(element,kAXTitleAttribute),ADNativeAttribute(element,kAXDescriptionAttribute)) : @"";
    NSMutableDictionary *record = [@{@"id":elementIdentity(state,object), @"role":role, @"subrole":subrole,
        @"label":boundedValue(label), @"value":value, @"enabled":ADNativeAttribute(element,kAXEnabledAttribute) ?: @YES,
        @"focused":ADNativeAttribute(element,kAXFocusedAttribute) ?: @NO, @"selected":ADNativeAttribute(element,kAXSelectedAttribute) ?: @NO,
        @"secure":@(secure), @"bounds":ADNativeRect(shown), @"clipped":@(!complete)} mutableCopy];
    if (numeric) record[@"bounds_source"] = @"native_container";
    id required = ADNativeAttribute(element,CFSTR("AXRequired"));
    if (required && CFGetTypeID((__bridge CFTypeRef)required)==CFBooleanGetTypeID()) record[@"required"] = required;
    BOOL textual = [@[@"AXStaticText",@"AXHeading",@"AXTextField",@"AXTextArea",@"AXComboBox",@"AXIncrementor"] containsObject:role];
    record[@"text_incomplete"] = @(!secure && textual && ((!complete && value==NSNull.null) || ([value isKindOfClass:NSString.class] && [value length]>=1500)));
    if (dialogID) record[@"parent_dialog_id"] = dialogID;
    if ([role isEqual:@"AXLink"]) {
        id url = ADNativeAttribute(element,kAXURLAttribute);
        if ([url isKindOfClass:NSURL.class]) record[@"url"] = [url absoluteString];
        else if ([url isKindOfClass:NSString.class]) record[@"url"] = url;
    }
    return record;
}

// Native sheets are distinguished from HTML dialogs by their ancestry outside any AXWebArea.
static NSString *recordDialog(ADNativeBrowserState *state, id object, NSString *role, BOOL web, NSMutableArray *dialogs) {
    AXUIElementRef element = (__bridge AXUIElementRef)object;
    NSString *subrole = ADNativeAttribute(element,kAXSubroleAttribute) ?: @"";
    NSDictionary *surface = web ? nil : ADNativeSurface(state,element);
    CGRect rect = ADNativeElementBounds(element);
    if (!surface || CGRectIsEmpty(rect)) return nil;
    NSString *identifier = elementIdentity(state,object);
    [dialogs addObject:[@{@"id":identifier,@"role":role,@"subrole":subrole,@"native":@YES,
        @"label":ADNativeAttribute(element,kAXTitleAttribute) ?: @"",@"bounds":ADNativeRect(rect),
        @"role_description":ADNativeAttribute(element,kAXRoleDescriptionAttribute) ?: @""} mutableCopy]];
    return identifier;
}

// The first visible outer web area supplies native page identity; iframes and later popup areas cannot replace it.
static void recordWebArea(ADNativeBrowserState *state, AXUIElementRef element, NSDictionary *scope, CGRect rect, NSMutableDictionary *output) {
    if ([scope[@"web"] boolValue] || output[@"web_bounds"]) return;
    CGRect shown = CGRectIntersection([scope[@"clip"] rectValue],rect);
    if (CGRectIsEmpty(shown)) return;
    output[@"web_bounds"] = ADNativeRect(shown);
    BOOL visible = ADNativeHitOwned(state,CGPointMake(CGRectGetMidX(shown),CGRectGetMidY(shown)),element);
    for (NSValue *offset in @[[NSValue valueWithPoint:CGPointMake(.25,.25)],[NSValue valueWithPoint:CGPointMake(.75,.25)],
                              [NSValue valueWithPoint:CGPointMake(.25,.75)],[NSValue valueWithPoint:CGPointMake(.75,.75)]]) {
        if (visible) break;
        CGPoint ratio = offset.pointValue;
        visible = ADNativeHitOwned(state,CGPointMake(shown.origin.x+shown.size.width*ratio.x,shown.origin.y+shown.size.height*ratio.y),element);
    }
    if (!visible) return;
    NSString *url = ADNativeHTTPURL(ADNativeAttribute(element,kAXURLAttribute));
    if (url) { output[@"url"] = url; output[@"document_url"] = url; }
}

// Children outside the viewport are pruned before reading their text or values.
static void walkVisible(ADNativeBrowserState *state, id object, NSDictionary *scope, NSMutableDictionary *output,
                        NSUInteger *remaining, NSMutableArray *webAreas) {
    if (!*remaining || [scope[@"depth"] unsignedIntegerValue]>35 || [output[@"controls"] count]>=300) return;
    *remaining -= 1;AXUIElementRef element = (__bridge AXUIElementRef)object;
    CGRect clip = [scope[@"clip"] rectValue], rect = ADNativeElementBounds(element);
    NSDictionary *surface = [scope[@"web"] boolValue] ? nil : ADNativeSurface(state,element);
    if (surface) clip = [surface[@"bounds"] rectValue];
    if (!CGRectIsEmpty(rect) && !CGRectIntersectsRect(rect,clip)) return;
    NSString *role = ADNativeAttribute(element,kAXRoleAttribute) ?: @"";
    // Read the tab strip before a long application form can exhaust the viewport budget.
    if (webAreas && [role isEqual:@"AXWebArea"]) {
        [webAreas addObject:@[object,scope]];
        return;
    }
    BOOL web = [scope[@"web"] boolValue] || [role isEqual:@"AXWebArea"];
    if (!web && ADNativeIsDialog(element)) {
        output[@"native_modal"] = @YES;
        if (!surface) return;
    }
    BOOL tabs = !web && ([scope[@"tabs"] boolValue] || [role isEqual:@"AXTabGroup"]);
    NSString *newDialog = recordDialog(state,object,role,web,output[@"dialogs"]), *dialogID = newDialog ?: scope[@"dialog"];
    if (newDialog && scope[@"dialog"]) { NSMutableDictionary *dialog = [output[@"dialogs"] lastObject];dialog[@"parent_dialog_id"] = scope[@"dialog"]; }
    NSSet *roles = [NSSet setWithArray:@[@"AXStaticText",@"AXTextField",@"AXTextArea",@"AXComboBox",@"AXIncrementor",@"AXButton",@"AXCheckBox",@"AXRadioButton",@"AXPopUpButton",@"AXLink",@"AXHeading",@"AXMenuItem",@"AXMenuButton",@"AXImage",@"AXBrowser",@"AXOutline",@"AXTable"]];
    if (dialogID) roles = [NSSet setWithArray:@[@"AXButton",@"AXTextField",@"AXComboBox",@"AXBrowser",@"AXOutline",@"AXTable"]];
    NSMutableDictionary *record = [roles containsObject:role] ? controlRecord(state,object,clip,dialogID) : nil;
    if (record) {
        record[@"web_content"] = @(web);
        if (dialogID && !web && [ADNativeAttribute(element,kAXIdentifierAttribute) isEqual:@"PathTextField"])
            record[@"picker_path_field"] = @YES;
        [output[@"controls"] addObject:record];
        if (tabs && [role isEqual:@"AXRadioButton"]) {
            if (![record[@"selected"] boolValue] && [record[@"value"] isKindOfClass:NSNumber.class]) record[@"selected"] = @([record[@"value"] boolValue]);
            [output[@"tabs"] addObject:@{@"id":record[@"id"],@"label":record[@"label"],@"selected":record[@"selected"]}];
        }
    }
    // The chooser protocol needs the file-list container role, never the filenames or sidebar entries inside it.
    if (dialogID && [@[@"AXBrowser",@"AXOutline",@"AXTable",@"AXList",@"AXCollection",@"AXGrid",@"AXRow",@"AXCell"] containsObject:role]) return;
    if ([role isEqual:@"AXWebArea"]) recordWebArea(state,element,scope,rect,output);
    if ([role isEqual:@"AXWebArea"] || [role isEqual:@"AXScrollArea"]) clip = CGRectIntersection(clip,rect);
    NSArray *children = ADNativeAttribute(element,kAXVisibleChildrenAttribute) ?: ADNativeAttribute(element,kAXChildrenAttribute);
    NSMutableDictionary *next = [@{@"clip":[NSValue valueWithRect:clip],@"web":@(web),@"tabs":@(tabs),@"depth":@([scope[@"depth"] unsignedIntegerValue]+1)} mutableCopy];
    if (dialogID) next[@"dialog"] = dialogID;
    for (id child in children) { if (!ADNativeCheckLease(state,nil)) break;walkVisible(state,child,next,output,remaining,webAreas); }
}

// Repeated reads hash the same visible state rather than generating fresh random snapshot IDs.
static NSString *observationHash(NSDictionary *observation) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:observation options:NSJSONWritingSortedKeys error:nil];
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];CC_SHA256(data.bytes,(CC_LONG)data.length,digest);
    NSMutableString *hash = [NSMutableString new];
    for (NSUInteger index=0; index<sizeof(digest); index++) [hash appendFormat:@"%02x",digest[index]];
    return hash;
}

// Visible text is bounded and keeps the native control records as its audit evidence.
void ADNativeSummarizeObservation(NSMutableDictionary *result) {
    NSMutableArray *texts = [NSMutableArray new];
    for (NSDictionary *control in result[@"controls"]) {
        for (NSString *key in [control[@"web_content"] boolValue] ? @[@"label",@"value"] : @[]) {
            id value = control[key];
            if ([value isKindOfClass:NSString.class] && [value length]) [texts addObject:value];
        }
        NSString *label = [control[@"label"] lowercaseString];
        if (!result[@"url"] && ![control[@"web_content"] boolValue] && [@[@"AXTextField",@"AXComboBox"] containsObject:control[@"role"]] && ([label containsString:@"address"] || [label containsString:@"search bar"])) {
            NSString *url = ADNativeHTTPURL(control[@"value"]);
            if (url) result[@"url"] = url;
        }
    }
    NSString *text = [texts componentsJoinedByString:@"\n"];
    result[@"text"] = text.length>24000 ? [text substringToIndex:24000] : text;
    result[@"text_capped"] = @(text.length>24000);
    BOOL incomplete = NO;for (NSDictionary *control in result[@"controls"]) if ([control[@"web_content"] boolValue] && [control[@"text_incomplete"] boolValue]) incomplete = YES;
    result[@"text_truncated"] = @(text.length>24000 || incomplete);result[@"screenshot_needed"] = @(incomplete);
    for (NSDictionary *tab in result[@"tabs"]) if ([tab[@"selected"] boolValue]) result[@"active_tab_id"] = tab[@"id"];
}

// Private typed strings remain private even if a native chooser displays the submitted path afterward.
static id redactObservation(id value, NSArray<NSString *> *redactions) {
    if ([value isKindOfClass:NSString.class]) {
        for (NSString *secret in redactions) if (secret.length) value = [value stringByReplacingOccurrencesOfString:secret withString:@"[redacted]"];
    } else if ([value isKindOfClass:NSArray.class]) {
        NSMutableArray *items = [NSMutableArray new];for (id item in value) [items addObject:redactObservation(item,redactions)];return items;
    } else if ([value isKindOfClass:NSDictionary.class]) {
        NSMutableDictionary *items = [NSMutableDictionary new];for (id key in value) items[key] = redactObservation(value[key],redactions);return items;
    }
    return value;
}

// Only the owned, visible window is inspected; screenshots are optional and stay window scoped.
NSDictionary *ADNativeObserve(ADNativeBrowserState *state, BOOL screenshot, NSError **error) {
    if (!ADNativeValidateWindow(state,NO,error) || !ADNativeRefreshSurfaces(state,error)) return nil;
    state.elements = [NSMutableDictionary new];
    NSMutableDictionary *result = [@{@"pid":@(state.pid),@"window_id":@(state.windowID),@"generation":@(state.generation),
        @"bounds":ADNativeRect(ADNativeWorkspaceBounds(state)),@"window_bounds":ADNativeRect(state.bounds),
        @"title":ADNativeAttribute((__bridge AXUIElementRef)state.windowElement,kAXTitleAttribute) ?: @"",
        @"controls":[NSMutableArray new],@"tabs":[NSMutableArray new],@"dialogs":[NSMutableArray new]} mutableCopy];
    NSUInteger remaining = 1500;
    NSMutableArray *webAreas = [NSMutableArray new];
    walkVisible(state,state.windowElement,@{@"clip":[NSValue valueWithRect:state.bounds],@"depth":@0},result,&remaining,webAreas);
    for (NSArray *area in webAreas) {
        if (!ADNativeCheckLease(state,error)) return nil;
        walkVisible(state,area[0],area[1],result,&remaining,nil);
    }
    if (!ADNativeCheckLease(state,error)) return nil;
    result[@"controls_truncated"] = @(!remaining || [result[@"controls"] count]>=300);
    result[@"native_modal"] = @([result[@"native_modal"] boolValue] || state.nativeSurfaces.count > 0);
    ADNativeSummarizeObservation(result);result = redactObservation(result,state.redactions);result[@"snapshot_id"] = observationHash(result);
    if (screenshot && ![result[@"native_modal"] boolValue] && remaining && [result[@"controls"] count]<300) {
        NSDictionary *image = ADNativeCaptureWindow(state.pid,state.windowID,state.bounds,error);
        if (!image) return nil;
        if (!ADNativeRefreshSurfaces(state,error) || state.nativeSurfaces.count || !ADNativeCheckLease(state,error)) return nil;
        result[@"screenshot"] = image;
    }
    state.observation = result;return result;
}

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
