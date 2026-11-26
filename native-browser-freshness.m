#import "native-browser-input.h"

// Diagnostics contain fixed field names only, never page text, URLs, credentials or input values.
static BOOL staleObservation(NSArray<NSString *> *keys, NSError **error) {
    NSString *message = [NSString stringWithFormat:@"The visible page changed (%@). Observe again before acting.",[keys componentsJoinedByString:@", "]];
    if (error) *error = [NSError errorWithDomain:@"ApplyDeskNativeBrowser" code:3 userInfo:
        @{NSLocalizedDescriptionKey:message,@"native_code":@"stale_observation",@"changed_keys":keys}];
    return NO;
}

// Fixed key lists make state comparisons explicit and prevent page-provided diagnostic names.
static NSArray<NSString *> *changedKeys(NSDictionary *before, NSDictionary *after, NSArray<NSString *> *keys) {
    NSMutableArray *changed = [NSMutableArray new];
    for (NSString *key in keys)
        if (![(before[key] ?: NSNull.null) isEqual:(after[key] ?: NSNull.null)]) [changed addObject:key];
    return changed;
}

// A repeated or missing ID is ambiguous even when the remaining visible metadata happens to match.
static NSDictionary *uniqueControl(NSDictionary *observation, NSString *identifier) {
    NSDictionary *match = nil;
    for (NSDictionary *control in observation[@"controls"]) if ([control[@"id"] isEqual:identifier]) {
        if (match) return nil;
        match = control;
    }
    return match;
}

// Targeted input tolerates unrelated animation but preserves every action-relevant control property.
static BOOL stableTarget(NSDictionary *prior, NSDictionary *fresh, NSDictionary *params, NSError **error) {
    NSDictionary *before = uniqueControl(prior,params[@"element_id"]), *after = uniqueControl(fresh,params[@"element_id"]);
    if (!before || !after) return staleObservation(@[@"target.id"],error);
    NSArray *keys = @[@"id",@"role",@"subrole",@"label",@"value",@"enabled",@"secure",@"bounds",@"web_content",@"parent_dialog_id",@"url",@"selected"];
    NSMutableArray *changed = [NSMutableArray new];
    for (NSString *key in changedKeys(before,after,keys)) [changed addObject:[@"target." stringByAppendingString:key]];
    if ([params[@"action"] isEqual:@"key"] && (![before[@"focused"] boolValue] || ![after[@"focused"] boolValue])) [changed addObject:@"target.focused"];
    return changed.count ? staleObservation(changed,error) : YES;
}

// Untargeted scrolling transmits no form data and tolerates text changes within the same measured viewport.
static BOOL stableViewport(NSDictionary *prior, NSDictionary *fresh, NSError **error) {
    NSArray *changed = changedKeys(prior,fresh,@[@"bounds",@"web_bounds",@"native_modal",@"dialogs"]);
    if (changed.count) return staleObservation(changed,error);
    if (![fresh[@"url"] isKindOfClass:NSString.class] || ![fresh[@"url"] length]) return staleObservation(@[@"url"],error);
    for (NSString *name in @[@"bounds",@"web_bounds"]) {
        NSDictionary *box = fresh[name];
        if (![box isKindOfClass:NSDictionary.class]) return staleObservation(@[name],error);
        for (NSString *key in @[@"x",@"y",@"width",@"height"])
            if (![box[key] isKindOfClass:NSNumber.class] || !isfinite([box[key] doubleValue])) return staleObservation(@[name],error);
        if ([box[@"width"] doubleValue]<=0 || [box[@"height"] doubleValue]<=0) return staleObservation(@[name],error);
    }
    return YES;
}

// Requests bind the last delivered snapshot before a new read can replace the helper's observation.
BOOL ADNativeValidateObservation(NSDictionary *prior, NSDictionary *fresh, NSDictionary *params, NSError **error) {
    if (!prior || ![params[@"snapshot_id"] isEqual:prior[@"snapshot_id"]]) return staleObservation(@[@"snapshot_id"],error);
    if (![params[@"generation"] isEqual:prior[@"generation"]]) return staleObservation(@[@"generation"],error);
    NSArray *binding = changedKeys(prior,fresh,@[@"pid",@"window_id",@"generation",@"active_tab_id",@"url"]);
    if (binding.count) return staleObservation(binding,error);
    if ([prior[@"snapshot_id"] isEqual:fresh[@"snapshot_id"]]) return YES;
    NSArray *changes = changedKeys(prior,fresh,@[@"title",@"text",@"controls",@"tabs",@"dialogs",@"bounds",@"web_bounds",@"native_modal",@"text_truncated",@"controls_truncated"]);
    if (!changes.count) changes = @[@"snapshot_id"];
    if (params[@"final_permit"] || params[@"x"] || params[@"y"]) return staleObservation(changes,error);
    if (![prior[@"active_tab_id"] isKindOfClass:NSString.class] || ![prior[@"active_tab_id"] length]) return staleObservation(@[@"active_tab_id"],error);
    NSString *action = params[@"action"], *key = [params[@"key"] lowercaseString];
    if ([action isEqual:@"key"] && [@[@"cmd+l",@"cmd+t"] containsObject:key] && !params[@"element_id"]) return YES;
    if ([action isEqual:@"scroll"] && !params[@"element_id"]) return stableViewport(prior,fresh,error);
    if (params[@"element_id"] && [@[@"click",@"type",@"key",@"scroll"] containsObject:action]) return stableTarget(prior,fresh,params,error);
    return staleObservation(changes,error);
}
