#import "../native/native-browser.h"

// Metadata reflects the actual Chrome New Tab strip captured on November 17, without page content.
static NSMutableDictionary *observedTabHit(void) {
    NSDictionary *strip = @{@"x":@136,@"y":@56,@"width":@256,@"height":@41};
    return [@{@"tab_role":@"AXRadioButton",@"tab_subrole":@"AXTabButton",@"group_role":@"AXTabGroup",
        @"hit_role":@"AXGroup",@"same_container":@YES,@"owned_native_ancestry":@YES,@"hit_child_count":@0,
        @"tab_bounds":strip,@"group_bounds":strip,@"hit_bounds":strip,@"x":@264,@"y":@76.5} mutableCopy];
}

// The native overlay may span several tabs, but the requested point must remain inside its exact tab.
static void realNativeOverlay(void) {
    NSMutableDictionary *evidence = observedTabHit();
    NSCAssert(ADNativeTabOverlayMatches(evidence),@"Observed native Chrome overlay accepted");
    evidence[@"group_bounds"] = @{@"x":@136,@"y":@56,@"width":@512,@"height":@41};
    evidence[@"hit_bounds"] = evidence[@"group_bounds"];
    NSCAssert(ADNativeTabOverlayMatches(evidence),@"An exact shared strip may contain multiple tabs");
    evidence[@"x"] = @500;
    NSCAssert(!ADNativeTabOverlayMatches(evidence),@"Another tab's point cannot target this tab");
}

// Web tabs, dialogs, another window and unrelated native overlays cannot use this exception.
static void rejectUnrelatedHits(void) {
    NSDictionary *variants = @{@"tab_role":@"AXButton",@"tab_subrole":@"",@"group_role":@"AXWebArea",
        @"hit_role":@"AXButton",@"same_container":@NO,@"owned_native_ancestry":@NO,@"hit_child_count":@1,
        @"hit_bounds":@{@"x":@136,@"y":@55,@"width":@256,@"height":@41},
        @"tab_bounds":@{@"x":@135,@"y":@56,@"width":@256,@"height":@41},@"x":@10};
    for (NSString *key in variants) {
        NSMutableDictionary *evidence = observedTabHit();evidence[key] = variants[key];
        NSCAssert(!ADNativeTabOverlayMatches(evidence),@"Unrelated hit metadata must be rejected");
    }
    NSMutableDictionary *empty = observedTabHit();empty[@"tab_bounds"] = @{};
    NSCAssert(!ADNativeTabOverlayMatches(empty),@"Invisible or empty tab bounds rejected");
    NSCAssert(!ADNativeTabOverlayMatches(@{}),@"Missing proof rejected");
}

int main(void) {
    @autoreleasepool { realNativeOverlay();rejectUnrelatedHits();puts("Observed native tab overlay and unrelated-window/web/overlay exclusions passed."); }
    return 0;
}
