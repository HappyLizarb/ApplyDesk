#import "../native-browser-window.h"

// Geometry and focus flags come from the actual visible Chrome autocomplete capture on November 17.
static NSMutableDictionary *observedFieldHit(void) {
    NSDictionary *parent = @{@"x":@165,@"y":@80,@"width":@507,@"height":@34};
    return [@{@"field_role":@"AXTextField",@"secure":@NO,@"focused":@YES,@"app_focus":@YES,
        @"native_toolbar_ancestry":@YES,@"owned_overlay":@YES,@"hit_role":@"AXGroup",@"hit_child_count":@0,
        @"field_bounds":@{@"x":@215,@"y":@85,@"width":@356,@"height":@24},
        @"parent_bounds":parent,@"hit_bounds":parent,@"x":@393,@"y":@97} mutableCopy];
}

// The field is usable only while both the native focus and the exact owned overlay relationship hold.
static void focusedNativeOverlay(void) {
    NSMutableDictionary *evidence = observedFieldHit();
    NSCAssert(ADNativeFieldOverlayMatches(evidence),@"Observed focused native field remains visible through its autocomplete hit surface");
    evidence[@"field_role"] = @"AXComboBox";
    NSCAssert(ADNativeFieldOverlayMatches(evidence),@"Native editable combo uses the same explicit proof");
    evidence[@"x"] = @170;
    NSCAssert(!ADNativeFieldOverlayMatches(evidence),@"A point outside the field cannot target it");
}

// Web fields, passwords, other popups, active controls and geometry changes retain strict rejection.
static void rejectUnrelatedFields(void) {
    NSDictionary *variants = @{@"field_role":@"AXButton",@"secure":@YES,@"focused":@NO,@"app_focus":@NO,
        @"native_toolbar_ancestry":@NO,@"owned_overlay":@NO,@"hit_role":@"AXButton",@"hit_child_count":@1,
        @"hit_bounds":@{@"x":@165,@"y":@79,@"width":@507,@"height":@34},
        @"field_bounds":@{@"x":@160,@"y":@85,@"width":@356,@"height":@24}};
    for (NSString *key in variants) {
        NSMutableDictionary *evidence = observedFieldHit();evidence[key] = variants[key];
        NSCAssert(!ADNativeFieldOverlayMatches(evidence),@"Every required relationship must hold");
    }
    NSMutableDictionary *empty = observedFieldHit();empty[@"field_bounds"] = @{};
    NSCAssert(!ADNativeFieldOverlayMatches(empty),@"Missing visible field bounds rejected");
    NSCAssert(!ADNativeFieldOverlayMatches(@{}),@"Missing proof rejected");
}

int main(void) {
    @autoreleasepool { focusedNativeOverlay();rejectUnrelatedFields();puts("Observed focused native toolbar overlay and unrelated field exclusions passed."); }
    return 0;
}
