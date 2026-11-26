#import "../native-browser-input.h"

// Pure fixture observations never inspect Chrome or generate native input.
static NSMutableDictionary *observation(NSString *snapshot) {
    NSDictionary *field = @{@"id":@"n4",@"role":@"AXTextField",@"subrole":@"",@"label":@"First name",
        @"value":@"private-existing-value",@"enabled":@YES,@"secure":@NO,@"focused":@YES,@"selected":@NO,
        @"web_content":@YES,@"bounds":@{@"x":@10,@"y":@20,@"width":@200,@"height":@24}};
    return [@{@"snapshot_id":snapshot,@"pid":@42,@"window_id":@77,@"generation":@3,
        @"active_tab_id":@"n1",@"url":@"https://example.test/application",@"title":@"Application",
        @"bounds":@{@"x":@10,@"y":@20,@"width":@800,@"height":@900},
        @"web_bounds":@{@"x":@10,@"y":@120,@"width":@800,@"height":@800},@"native_modal":@NO,@"dialogs":@[],
        @"text":@"Page content",@"controls":@[field],@"tabs":@[@{@"id":@"n1",@"selected":@YES}]} mutableCopy];
}

// Page text and another control's focus can change while the intended editable control stays exact.
static void unrelatedChanges(void) {
    NSDictionary *prior = observation(@"before");NSMutableDictionary *fresh = observation(@"after");NSError *error = nil;
    fresh[@"text"] = @"An animated banner changed";
    NSDictionary *params = @{@"snapshot_id":@"before",@"generation":@3,@"action":@"type",@"element_id":@"n4"};
    NSCAssert(ADNativeValidateObservation(prior,fresh,params,&error),@"Unrelated text permits exact observed target");
    NSMutableDictionary *field = [fresh[@"controls"][0] mutableCopy];field[@"focused"] = @NO;fresh[@"controls"] = @[field];
    NSCAssert(ADNativeValidateObservation(prior,fresh,params,&error),@"Typing clicks exact target so previous focus may change");
    NSMutableDictionary *keyParams = [params mutableCopy];keyParams[@"action"] = @"key";keyParams[@"key"] = @"down";
    NSCAssert(!ADNativeValidateObservation(prior,fresh,keyParams,&error),@"Targeted keyboard event requires same focused target");
    for (NSString *key in @[@"cmd+l",@"cmd+t"]) {
        NSDictionary *navigation = @{@"snapshot_id":@"before",@"generation":@3,@"action":@"key",@"key":key};
        NSCAssert(ADNativeValidateObservation(prior,fresh,navigation,&error),@"Global Chrome navigation tolerates unrelated page animation");
    }
    NSCAssert([prior[@"snapshot_id"] isEqual:@"before"],@"Prior evidence remains unchanged");
}

// A changed target's value, semantics or geometry must be observed again before any input.
static void changedTargets(void) {
    NSDictionary *prior = observation(@"before");NSError *error = nil;
    NSDictionary *params = @{@"snapshot_id":@"before",@"generation":@3,@"action":@"click",@"element_id":@"n4"};
    NSDictionary *variants = @{@"role":@"AXButton",@"label":@"Secret changed label",@"value":@"Secret changed value",
        @"enabled":@NO,@"secure":@YES,@"bounds":@{},@"selected":@YES,@"web_content":@NO};
    for (NSString *key in variants) {
        NSMutableDictionary *fresh = observation(@"after"), *field = [fresh[@"controls"][0] mutableCopy];
        field[key] = variants[key];fresh[@"controls"] = @[field];
        NSCAssert(!ADNativeValidateObservation(prior,fresh,params,&error),@"Changed target rejected");
        NSCAssert([error.userInfo[@"native_code"] isEqual:@"stale_observation"],@"Explicit definitely-unsent error code");
        NSCAssert([error.userInfo[@"changed_keys"] containsObject:[@"target." stringByAppendingString:key]],@"Diagnostic identifies key");
        NSCAssert(![error.description containsString:@"Secret"] && ![error.description containsString:@"private-existing"],@"Diagnostic never exposes values");
    }
    NSMutableDictionary *missing = observation(@"after");missing[@"controls"] = @[];
    NSCAssert(!ADNativeValidateObservation(prior,missing,params,&error),@"Missing target rejected");
    missing[@"controls"] = @[prior[@"controls"][0],prior[@"controls"][0]];
    NSCAssert(!ADNativeValidateObservation(prior,missing,params,&error),@"Duplicate target identity rejected");
}

// Window, generation, active tab and URL binding apply even to safe browser-navigation shortcuts.
static void changedBindings(void) {
    NSDictionary *prior = observation(@"before");NSError *error = nil;
    NSDictionary *params = @{@"snapshot_id":@"before",@"generation":@3,@"action":@"key",@"key":@"cmd+t"};
    NSDictionary *variants = @{@"pid":@43,@"window_id":@78,@"generation":@4,@"active_tab_id":@"n2",@"url":@"https://other.test/private"};
    for (NSString *key in variants) {
        NSMutableDictionary *fresh = observation(@"after");fresh[key] = variants[key];
        NSCAssert(!ADNativeValidateObservation(prior,fresh,params,&error),@"Binding change rejected");
        NSCAssert([error.userInfo[@"changed_keys"] containsObject:key],@"Binding diagnostic names changed field");
        NSCAssert(![error.description containsString:@"https://"],@"URL never included in diagnostics");
    }
    NSMutableDictionary *older = [params mutableCopy];older[@"snapshot_id"] = @"older";
    NSCAssert(!ADNativeValidateObservation(prior,prior,older,&error),@"Request must bind last delivered observation");
    older[@"snapshot_id"] = @"before";older[@"generation"] = @2;
    NSCAssert(!ADNativeValidateObservation(prior,prior,older,&error),@"Stop or previous generation cannot act");
    NSMutableDictionary *unknown = observation(@"after");[unknown removeObjectForKey:@"active_tab_id"];
    NSCAssert(!ADNativeValidateObservation(prior,unknown,params,&error),@"Unknown current tab cannot reuse prior scope");
}

// Final authority, screenshot points and non-navigation untargeted keys keep full snapshot equality.
static void strictActions(void) {
    NSDictionary *prior = observation(@"before");NSMutableDictionary *fresh = observation(@"after");NSError *error = nil;
    fresh[@"text"] = @"Changed banner";
    NSArray *actions = @[@{@"action":@"click",@"element_id":@"n4",@"final_permit":@"reserved-attempt"},
        @{@"action":@"click",@"element_id":@"n4",@"final_permit":@YES},
        @{@"action":@"click",@"x":@50,@"y":@50},@{@"action":@"key",@"key":@"return"},
        @{@"action":@"key",@"key":@"cmd+a"},@{@"action":@"key",@"key":@"cmd+home"},
        @{@"action":@"scroll",@"delta_y":@400,@"x":@100,@"y":@200}];
    for (NSDictionary *action in actions) {
        NSMutableDictionary *params = [action mutableCopy];params[@"snapshot_id"] = @"before";params[@"generation"] = @3;
        NSCAssert(!ADNativeValidateObservation(prior,fresh,params,&error),@"Strict action rejects changed visible state");
        NSCAssert(ADNativeValidateObservation(prior,prior,params,&error),@"Same strict snapshot remains usable");
    }
}

// Page loading can change text while scrolling stays bound to the exact window, URL and viewport.
static void stablePageScrolling(void) {
    NSDictionary *prior = observation(@"before");NSMutableDictionary *fresh = observation(@"after");NSError *error = nil;
    NSDictionary *params = @{@"snapshot_id":@"before",@"generation":@3,@"action":@"scroll",@"delta_y":@400};
    fresh[@"text"] = @"Description finished loading";fresh[@"controls"] = @[];
    NSCAssert(ADNativeValidateObservation(prior,fresh,params,&error),@"Loading text does not block a nontransmitting viewport scroll");
    NSDictionary *variants = @{@"bounds":@{},@"web_bounds":@{},@"native_modal":@YES,@"dialogs":@[@{@"id":@"dialog"}],
        @"url":@"https://other.test/",@"active_tab_id":@"other-tab",@"generation":@4,@"window_id":@78,@"pid":@43};
    for (NSString *key in variants) {
        NSMutableDictionary *changed = [fresh mutableCopy];changed[key] = variants[key];
        NSCAssert(!ADNativeValidateObservation(prior,changed,params,&error),@"Scroll cannot reuse changed page or viewport scope");
    }
    for (id invalid in @[NSNull.null,@{},@{@"x":@10,@"y":@120,@"width":@0,@"height":@800}]) {
        NSMutableDictionary *unknownPrior = [prior mutableCopy], *unknownFresh = [fresh mutableCopy];
        unknownPrior[@"web_bounds"] = invalid;unknownFresh[@"web_bounds"] = invalid;
        NSCAssert(!ADNativeValidateObservation(unknownPrior,unknownFresh,params,&error),@"Equal but unmeasured viewports are insufficient");
    }
    NSMutableDictionary *final = [params mutableCopy];final[@"final_permit"] = @"reserved-attempt";
    NSCAssert(!ADNativeValidateObservation(prior,fresh,final,&error),@"Final authority still requires exact full snapshot");
}

int main(void) {
    @autoreleasepool { unrelatedChanges();changedTargets();changedBindings();strictActions();stablePageScrolling();puts("Native freshness target, navigation, viewport scroll, scope, strict-action and private-diagnostic checks passed."); }
    return 0;
}
