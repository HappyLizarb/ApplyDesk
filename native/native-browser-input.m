#import "native-browser.h"
#import <Carbon/Carbon.h>
#include <unistd.h>

// The finite key vocabulary excludes system/app shortcuts unrelated to an owned browser task.
NSDictionary *ADNativeKeyDefinition(NSString *name) {
    NSDictionary *keys = @{@"tab":@(kVK_Tab),@"enter":@(kVK_Return),@"return":@(kVK_Return),@"escape":@(kVK_Escape),
        @"backspace":@(kVK_Delete),@"delete":@(kVK_ForwardDelete),@"up":@(kVK_UpArrow),@"down":@(kVK_DownArrow),
        @"left":@(kVK_LeftArrow),@"right":@(kVK_RightArrow),@"home":@(kVK_Home),@"end":@(kVK_End),
        @"pageup":@(kVK_PageUp),@"pagedown":@(kVK_PageDown),@"a":@(kVK_ANSI_A),@"l":@(kVK_ANSI_L),
        @"t":@(kVK_ANSI_T),@"w":@(kVK_ANSI_W),@"f":@(kVK_ANSI_F),@"g":@(kVK_ANSI_G)};
    NSSet *allowed = [NSSet setWithArray:@[@"tab",@"shift+tab",@"enter",@"return",@"escape",@"backspace",@"delete",@"up",@"down",@"left",@"right",@"home",@"end",@"pageup",@"pagedown",@"cmd+a",@"cmd+l",@"cmd+t",@"cmd+w",@"cmd+f",@"cmd+shift+g",@"cmd+home",@"cmd+end"]];
    name = name.lowercaseString;
    if (![allowed containsObject:name]) return nil;
    NSArray *parts = [name componentsSeparatedByString:@"+"];CGEventFlags flags = 0;
    if ([parts containsObject:@"cmd"]) flags |= kCGEventFlagMaskCommand;
    if ([parts containsObject:@"shift"]) flags |= kCGEventFlagMaskShift;
    return @{@"keycode":keys[parts.lastObject],@"flags":@(flags)};
}

// Each key pair goes through the real event stream only while the intended Chrome window is frontmost.
BOOL ADNativePostKey(ADNativeBrowserState *state, CGKeyCode key, CGEventFlags flags, NSError **error) {
    if (!ADNativeCheckLease(state,error) || !CGPreflightPostEventAccess()) return NO;
    if (!ADNativeValidateWindow(state,YES,error)) return NO;
    CGEventRef down = CGEventCreateKeyboardEvent(NULL,key,true), up = CGEventCreateKeyboardEvent(NULL,key,false);
    CGEventSetFlags(down,flags);CGEventSetFlags(up,flags);
    CGEventPost(kCGHIDEventTap,down);CGEventPost(kCGHIDEventTap,up);CFRelease(down);CFRelease(up);
    return YES;
}

// Control targets and screenshot points use the same owned-window hit test immediately before the click.
static BOOL clickPoint(ADNativeBrowserState *state, CGPoint point, AXUIElementRef expected, NSError **error) {
    NSDictionary *numeric = expected ? ADNativeNumericWrapper(state,(__bridge id)expected,state.bounds) : nil;
    if (numeric) expected = (__bridge AXUIElementRef)numeric[@"element"];
    if (!ADNativeValidateWindow(state,YES,error) || !ADNativeHitOwned(state,point,expected)) {
        if (error && !*error) *error = ADNativeError(@"The visible control moved or is covered. Observe again.");return NO;
    }
    CGEventRef down = CGEventCreateMouseEvent(NULL,kCGEventLeftMouseDown,point,kCGMouseButtonLeft);
    CGEventRef up = CGEventCreateMouseEvent(NULL,kCGEventLeftMouseUp,point,kCGMouseButtonLeft);
    CGEventSetFlags(down,0);CGEventSetFlags(up,0);
    CGEventPost(kCGHIDEventTap,down);CGEventPost(kCGHIDEventTap,up);CFRelease(down);CFRelease(up);
    return YES;
}

// Secret and ordinary typing both require the same currently focused native control.
static BOOL focusedTarget(ADNativeBrowserState *state, id target, NSString *stage, NSError **error) {
    if (!ADNativeValidateWindow(state,YES,error)) return NO;
    AXUIElementRef app = AXUIElementCreateApplication(state.pid);
    id focused = ADNativeAttribute(app,kAXFocusedUIElementAttribute);CFRelease(app);
    if (!target || !focused || !CFEqual((__bridge CFTypeRef)focused,(__bridge CFTypeRef)target)) {
        if (error) *error = ADNativeError([NSString stringWithFormat:@"The typing target lost native focus %@.",stage]);return NO;
    }
    return YES;
}

// Chrome completes Cmd-A asynchronously; settle before text or an explicit empty replacement's Backspace.
static BOOL prepareReplacement(ADNativeBrowserState *state, id target, NSString *text, NSError **error) {
    if (!ADNativePostKey(state,kVK_ANSI_A,kCGEventFlagMaskCommand,error)) return NO;
    for (NSUInteger step=0; step<5; step++) {
        usleep(30000);
        if (!ADNativeCheckLease(state,error)) return NO;
    }
    if (!focusedTarget(state,target,@"after selecting the field contents",error)) return NO;
    return text.length || ADNativePostKey(state,kVK_Delete,0,error);
}

// Text events explicitly carry no shortcut modifiers inherited from the preceding Cmd-A or Cmd-L.
static BOOL typeCharacters(ADNativeBrowserState *state, id target, NSString *text, NSError **error) {
    __block BOOL success = YES;__block NSError *failure = nil;__block NSUInteger sent = 0;
    [text enumerateSubstringsInRange:NSMakeRange(0,text.length) options:NSStringEnumerationByComposedCharacterSequences usingBlock:^(NSString *part, NSRange range, NSRange enclosing, BOOL *stop) {
        NSString *stage = [NSString stringWithFormat:@"during typing after %lu characters",(unsigned long)sent];
        if (!focusedTarget(state,target,stage,&failure) || part.length>128) { success = NO;*stop = YES;return; }
        UniChar characters[128];[part getCharacters:characters range:NSMakeRange(0,part.length)];
        CGEventRef down = CGEventCreateKeyboardEvent(NULL,0,true), up = CGEventCreateKeyboardEvent(NULL,0,false);
        CGEventSetFlags(down,0);CGEventSetFlags(up,0);
        CGEventKeyboardSetUnicodeString(down,part.length,characters);CGEventKeyboardSetUnicodeString(up,part.length,characters);
        CGEventPost(kCGHIDEventTap,down);CGEventPost(kCGHIDEventTap,up);CFRelease(down);CFRelease(up);
        sent += 1;
        usleep(12000 + arc4random_uniform(16000));
    }];
    if (!success && error) *error = failure ?: ADNativeError(@"The text contains an unsupported character sequence.");
    return success;
}

// macOS remembers the last Go to Folder path. Write that one native field atomically and verify it before Return.
static NSDictionary *setPickerPath(ADNativeBrowserState *state, id target, NSString *path, NSError **error) {
    AXUIElementRef field = (__bridge AXUIElementRef)target;
    if (!path.isAbsolutePath || ![ADNativeAttribute(field,kAXIdentifierAttribute) isEqual:@"PathTextField"]
        || !focusedTarget(state,target,@"in the file chooser",error)) {
        if (error && !*error) *error = ADNativeError(@"The native Go to Folder field is unavailable.");
        return nil;
    }
    if (![ADNativeAttribute(field,kAXValueAttribute) isEqual:path]
        && AXUIElementSetAttributeValue(field,kAXValueAttribute,(__bridge CFStringRef)path) != kAXErrorSuccess) {
        if (error) *error = ADNativeError(@"The file chooser could not accept the approved document path.");
        return nil;
    }
    for (NSUInteger attempt=0; attempt<12; attempt++) {
        if (!focusedTarget(state,target,@"after setting the file chooser path",error)) return nil;
        if ([ADNativeAttribute(field,kAXValueAttribute) isEqual:path]) return @{@"typed":@YES,@"private_value_matches":@YES};
        usleep(25000);
    }
    if (error) *error = ADNativeError(@"The native file chooser did not retain the approved document path.");
    return nil;
}

// Only a confirmed private chooser control uses the path setter; browser fields keep ordinary keyboard input.
static NSDictionary *typeTarget(ADNativeBrowserState *state, NSDictionary *params, NSDictionary *control, NSError **error) {
    NSString *text = params[@"text"];id target = state.elements[control[@"id"]];
    NSSet *editable = [NSSet setWithArray:@[@"AXTextField",@"AXTextArea",@"AXComboBox",@"AXIncrementor"]];
    if (![text isKindOfClass:NSString.class] || text.length>12000 || ![editable containsObject:control[@"role"]]
        || ([params[@"private"] boolValue] && ![control[@"secure"] boolValue] && !control[@"parent_dialog_id"])) {
        if (error) *error = ADNativeError(@"Typing requires an observed editable control with the correct privacy scope.");return nil;
    }
    Boolean numericEditable = false;
    if ([control[@"role"] isEqual:@"AXIncrementor"] &&
        (AXUIElementIsAttributeSettable((__bridge AXUIElementRef)target,kAXValueAttribute,&numericEditable)!=kAXErrorSuccess || !numericEditable)) {
        if (error) *error = ADNativeError(@"The observed numeric control is not editable.");return nil;
    }
    NSDictionary *bounds = control[@"bounds"];CGPoint point = CGPointMake([bounds[@"x"] doubleValue]+[bounds[@"width"] doubleValue]/2,[bounds[@"y"] doubleValue]+[bounds[@"height"] doubleValue]/2);
    if (!focusedTarget(state,target,@"before typing",nil)) {
        if (!clickPoint(state,point,(__bridge AXUIElementRef)target,error)) return nil;
        usleep(50000);if (!focusedTarget(state,target,@"after clicking the field",error)) return nil;
    }
    if ([params[@"private"] boolValue]) { if (!state.redactions) state.redactions = [NSMutableArray new];[state.redactions addObject:text]; }
    if ([params[@"private"] boolValue] && [params[@"replace"] boolValue] && ![control[@"secure"] boolValue]
        && ![control[@"web_content"] boolValue] && [control[@"parent_dialog_id"] length]
        && [ADNativeAttribute((__bridge AXUIElementRef)target,kAXIdentifierAttribute) isEqual:@"PathTextField"])
        return setPickerPath(state,target,text,error);
    if ([params[@"replace"] boolValue] && !prepareReplacement(state,target,text,error)) return nil;
    if (!typeCharacters(state,target,text,error)) return nil;
    id matches = NSNull.null;
    if ([params[@"private"] boolValue] && ![control[@"secure"] boolValue]) matches = @([ADNativeAttribute((__bridge AXUIElementRef)target,kAXValueAttribute) isEqual:text]);
    return @{@"typed":@YES,@"private_value_matches":matches};
}

// An email-method chooser opens credential fields; it cannot already contain inputs to submit.
static BOOL emailMethodNavigation(NSDictionary *page, NSDictionary *control, NSString *label) {
    if (![control[@"web_content"] boolValue] || ![@[@"AXButton",@"AXLink"] containsObject:control[@"role"]]
        || ![page[@"controls"] isKindOfClass:NSArray.class] || [page[@"controls_truncated"] boolValue]
        || [page[@"native_modal"] boolValue]) return NO;
    NSRegularExpression *choice = [NSRegularExpression regularExpressionWithPattern:
        @"^(?:sign[ -]?in|log[ -]?in|sign[ -]?up|register|create (?:an? )?account) (?:with|using|via) (?:e-?mail|email address)$" options:0 error:nil];
    if (![choice numberOfMatchesInString:label options:0 range:NSMakeRange(0,label.length)]) return NO;
    for (NSDictionary *field in page[@"controls"]) {
        if (![field[@"web_content"] boolValue]) continue;
        if ([field[@"secure"] boolValue] || [@[@"AXTextField",@"AXTextArea",@"AXComboBox",@"AXIncrementor"] containsObject:field[@"role"]]) return NO;
    }
    return YES;
}

// Submission, credential entry and implicit web-field Return retain their backend-only authority.
static BOOL requiresFinalPermit(NSDictionary *page, NSDictionary *control, NSString *action, NSString *key) {
    NSString *label = [control[@"label"] isKindOfClass:NSString.class] ? [control[@"label"] lowercaseString] : @"";
    NSRegularExpression *pattern = [NSRegularExpression regularExpressionWithPattern:@"\\b(submit|send application|finish|complete application|confirm application|sign[ -]?in|log[ -]?in|create (?:an? )?account|register|sign[ -]?up|withdraw|delete account|accept offer)\\b" options:0 error:nil];
    BOOL final = [pattern numberOfMatchesInString:label options:0 range:NSMakeRange(0,label.length)]>0;
    BOOL enter = [@[@"enter",@"return"] containsObject:key.lowercaseString];
    BOOL implicit = enter && [control[@"web_content"] boolValue] && [@[@"AXTextField",@"AXTextArea",@"AXIncrementor"] containsObject:control[@"role"]];
    BOOL navigation = emailMethodNavigation(page,control,label);
    return ([action isEqual:@"click"] || enter) && (implicit || (final && !navigation));
}

// Backend authentication authority is boolean; durable application attempt IDs can be consumed only once.
BOOL ADNativeAuthorizeEvent(ADNativeBrowserState *state, NSDictionary *params, NSDictionary *control, NSError **error) {
    id permit = params[@"final_permit"];
    BOOL permitted = [permit isKindOfClass:NSString.class] ? [permit length]>=8 : [permit isKindOfClass:NSNumber.class] && [permit boolValue];
    if (requiresFinalPermit(state.observation,control,params[@"action"],params[@"key"] ?: @"") && !permitted) {
        if (error) *error = ADNativeError(@"This control requires the protected account or reviewed submission operation.");return NO;
    }
    if ([permit isKindOfClass:NSString.class]) {
        if (!state.usedFinalPermits) state.usedFinalPermits = [NSMutableSet new];
        if ([state.usedFinalPermits containsObject:permit]) { if (error) *error = ADNativeError(@"This application submission was already attempted.");return NO; }
        [state.usedFinalPermits addObject:permit];
    }
    return YES;
}

// Scroll coordinates remain inside the visible web viewport or an explicitly observed control.
static BOOL scrollPage(ADNativeBrowserState *state, NSDictionary *params, CGPoint point, NSError **error) {
    double dx = [params[@"delta_x"] doubleValue], dy = [params[@"delta_y"] doubleValue];
    if (!isfinite(dx) || !isfinite(dy) || fabs(dx)>1200 || fabs(dy)>1200 || !ADNativeHitOwned(state,point,NULL)) {
        if (error) *error = ADNativeError(@"Scroll needs a current point inside the owned window and a bounded distance.");return NO;
    }
    if (!ADNativeValidateWindow(state,YES,error)) return NO;
    CGEventRef event = CGEventCreateScrollWheelEvent(NULL,kCGScrollEventUnitPixel,2,-(int32_t)dy,-(int32_t)dx);
    CGEventSetFlags(event,0);
    CGEventSetLocation(event,point);CGEventPost(kCGHIDEventTap,event);CFRelease(event);return YES;
}

// Coordinates default to the web viewport; explicit screenshot points are relative to the owned window.
static CGPoint actionPoint(ADNativeBrowserState *state, NSDictionary *params, NSDictionary *control) {
    NSDictionary *bounds = control[@"bounds"] ?: state.observation[@"web_bounds"] ?: ADNativeRect(state.bounds);
    NSDictionary *workspace = state.observation[@"bounds"] ?: ADNativeRect(state.bounds);
    if (params[@"x"] && params[@"y"]) return CGPointMake([workspace[@"x"] doubleValue]+[params[@"x"] doubleValue],[workspace[@"y"] doubleValue]+[params[@"y"] doubleValue]);
    return CGPointMake([bounds[@"x"] doubleValue]+[bounds[@"width"] doubleValue]/2,[bounds[@"y"] doubleValue]+[bounds[@"height"] doubleValue]/2);
}

// Screenshot clicks inherit any known control at that point, including its final-action boundary.
static NSDictionary *controlAtPoint(ADNativeBrowserState *state, NSDictionary *params, NSArray *controls) {
    if (!params[@"x"] || !params[@"y"]) return nil;
    CGPoint point = actionPoint(state,params,nil);NSDictionary *match = nil;double smallest = INFINITY;
    NSSet *roles = [NSSet setWithArray:@[@"AXButton",@"AXLink",@"AXRadioButton",@"AXCheckBox",@"AXTextField",@"AXTextArea",@"AXComboBox",@"AXIncrementor",@"AXPopUpButton"]];
    for (NSDictionary *control in controls) {
        NSDictionary *box = control[@"bounds"];CGRect rect = CGRectMake([box[@"x"] doubleValue],[box[@"y"] doubleValue],[box[@"width"] doubleValue],[box[@"height"] doubleValue]);
        double area = rect.size.width*rect.size.height;
        if ([roles containsObject:control[@"role"]] && CGRectContainsPoint(rect,point) && area<smallest) { match = control;smallest = area; }
    }
    return match;
}

// The operation vocabulary contains no raw script, arbitrary keycode, hidden selector, or AX setter.
static NSDictionary *performAction(ADNativeBrowserState *state, NSDictionary *params, NSDictionary *control, NSError **error) {
    NSString *action = params[@"action"], *key = params[@"key"] ?: @"";
    if (!ADNativeAuthorizeEvent(state,params,control,error)) return nil;
    if ([action isEqual:@"type"]) return typeTarget(state,params,control,error);
    if ([action isEqual:@"key"]) {
        NSDictionary *definition = ADNativeKeyDefinition(key);
        if (!definition) { if (error) *error = ADNativeError(@"Unsupported native browser key.");return nil; }
        if (!control && [@[@"enter",@"return"] containsObject:key.lowercaseString]) { if (error) *error = ADNativeError(@"Return requires an observed focused control.");return nil; }
        if ([key.lowercaseString isEqual:@"cmd+w"] && [state.observation[@"tabs"] count]<2) { if (error) *error = ADNativeError(@"Keep the last owned browser tab open.");return nil; }
        return ADNativePostKey(state,[definition[@"keycode"] unsignedShortValue],[definition[@"flags"] unsignedLongLongValue],error) ? @{} : nil;
    }
    CGPoint point = actionPoint(state,params,control);
    id target = control ? state.elements[control[@"id"]] : nil;
    if ([action isEqual:@"click"]) return clickPoint(state,point,(__bridge AXUIElementRef)target,error) ? @{} : nil;
    if ([action isEqual:@"scroll"]) return scrollPage(state,params,point,error) ? @{} : nil;
    if ([action isEqual:@"raise"]) return @{};
    if ([action isEqual:@"move"]) return ADNativeMove(state,params,error) ? @{} : nil;
    if (error) *error = ADNativeError(@"Unknown native browser operation.");return nil;
}

// Fresh visible evidence, window identity and generation must agree before any native input.
NSDictionary *ADNativeAction(ADNativeBrowserState *state, NSDictionary *params, NSError **error) {
    NSDictionary *prior = state.observation;
    NSDictionary *fresh = ADNativeObserve(state,NO,error);
    if (!fresh) return nil;
    if (!ADNativeValidateObservation(prior,fresh,params,error)) return nil;
    NSDictionary *control = nil;
    for (NSDictionary *candidate in fresh[@"controls"]) if ([candidate[@"id"] isEqual:params[@"element_id"]]) control = candidate;
    if (!control && [params[@"action"] isEqual:@"click"]) control = controlAtPoint(state,params,fresh[@"controls"]);
    if (!control && [params[@"action"] isEqual:@"key"]) for (NSDictionary *candidate in fresh[@"controls"]) if ([candidate[@"focused"] boolValue]) control = candidate;
    if (params[@"element_id"] && !control) { if (error) *error = ADNativeError(@"The observed control is no longer visible.");return nil; }
    if (!control && [params[@"action"] isEqual:@"type"]) { if (error) *error = ADNativeError(@"Typing requires an observed text control.");return nil; }
    if (control && ![control[@"enabled"] boolValue]) { if (error) *error = ADNativeError(@"The observed control is disabled.");return nil; }
    state.performingInput = YES;
    if (!ADNativeRaise(state,error)) { state.performingInput = NO;return nil; }
    NSDictionary *details = performAction(state,params,control,error);state.performingInput = NO;
    if (!details) return nil;
    usleep(100000);NSMutableDictionary *result = [ADNativeObserve(state,NO,error) mutableCopy];
    if (result) result[@"action_result"] = details;return result;
}

// Movement starts only on a screenshot-grounded empty native title region, never a tab or webpage control.
static BOOL titlePoint(ADNativeBrowserState *state, NSDictionary *params, CGPoint *point, NSError **error) {
    if (!params[@"x"] || !params[@"y"] || [params[@"y"] doubleValue]<0 || [params[@"y"] doubleValue]>35) {
        if (error) *error = ADNativeError(@"Choose an empty native title-bar point from the current screenshot before moving the window.");return NO;
    }
    *point = CGPointMake(state.bounds.origin.x+[params[@"x"] doubleValue],state.bounds.origin.y+[params[@"y"] doubleValue]);
    for (NSDictionary *control in state.observation[@"controls"]) {
        NSDictionary *box = control[@"bounds"];CGRect rect = CGRectMake([box[@"x"] doubleValue],[box[@"y"] doubleValue],[box[@"width"] doubleValue],[box[@"height"] doubleValue]);
        if (CGRectContainsPoint(rect,*point)) { if (error) *error = ADNativeError(@"The proposed title-bar point overlaps a visible control.");return NO; }
    }
    if (!ADNativeHitOwned(state,*point,NULL)) { if (error) *error = ADNativeError(@"The title-bar point is covered or outside the owned window.");return NO; }
    return YES;
}

// Native drag events move the owned window; no accessibility property is ever set.
BOOL ADNativeMove(ADNativeBrowserState *state, NSDictionary *params, NSError **error) {
    NSDictionary *bounds = params[@"bounds"];CGPoint start = CGPointZero;
    if (![bounds isKindOfClass:NSDictionary.class] || !titlePoint(state,params,&start,error)) return NO;
    double x = [bounds[@"x"] doubleValue], y = [bounds[@"y"] doubleValue];
    CGRect target = CGRectMake(x,y,state.bounds.size.width,state.bounds.size.height);
    if (!isfinite(x) || !isfinite(y) || !CGRectContainsRect(CGDisplayBounds(CGMainDisplayID()),target)) {
        if (error) *error = ADNativeError(@"The requested position must keep the owned window on the main display.");return NO;
    }
    CGPoint delta = CGPointMake(x-state.bounds.origin.x,y-state.bounds.origin.y), current = start;
    CGEventRef down = CGEventCreateMouseEvent(NULL,kCGEventLeftMouseDown,start,kCGMouseButtonLeft);
    CGEventSetFlags(down,0);CGEventPost(kCGHIDEventTap,down);CFRelease(down);
    BOOL moved = YES;
    for (NSUInteger step=1; step<=20; step++) {
        if (!ADNativeValidateWindow(state,YES,error)) { moved = NO;break; }
        current = CGPointMake(start.x+delta.x*step/20,start.y+delta.y*step/20);
        CGEventRef event = CGEventCreateMouseEvent(NULL,kCGEventLeftMouseDragged,current,kCGMouseButtonLeft);
        CGEventSetFlags(event,0);
        CGEventPost(kCGHIDEventTap,event);CFRelease(event);usleep(20000);
    }
    CGEventRef up = CGEventCreateMouseEvent(NULL,kCGEventLeftMouseUp,current,kCGMouseButtonLeft);
    CGEventSetFlags(up,0);CGEventPost(kCGHIDEventTap,up);CFRelease(up);
    if (!moved || !ADNativeValidateWindow(state,YES,error)) return NO;
    if (fabs(state.bounds.origin.x-x)>4 || fabs(state.bounds.origin.y-y)>4) { if (error) *error = ADNativeError(@"The window did not reach the requested position.");return NO; }
    return YES;
}
