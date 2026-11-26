#import "../chrome-approval.h"

// These fictional accessibility trees never inspect or interact with any running application.
static NSDictionary *prompt(NSString *role, NSString *subrole) {
    return @{@"role": role, @"subrole": subrole, @"children": @[
        @{@"role": @"AXStaticText", @"value": @"Allow remote debugging?"},
        @{@"role": @"AXStaticText", @"value": @"An external app wants full control over this Chrome session to debug it. This includes access to your saved data, cookies and site data, and the ability to navigate to any URL."},
        @{@"role": @"AXStaticText", @"value": @"Only web developers should turn on this feature, and only use it with trusted apps."},
        @{@"role": @"AXButton", @"title": @"Cancel", @"enabled": @YES},
        @{@"role": @"AXButton", @"title": @"Allow", @"enabled": @YES}]};
}

// Native containers, complete text and one enabled Allow control are all required together.
static void exactMatchChecks(void) {
    NSDictionary *sheet = prompt(@"AXSheet", @"");
    NSCAssert(ADChromeApprovalButtons(@[sheet]).count == 1, @"Expected exact native sheet");
    NSCAssert(ADChromeApprovalButtons(@[prompt(@"AXWindow", @"AXDialog")]).count == 1, @"Expected native dialog window");
    NSCAssert(ADChromeApprovalButtons(@[prompt(@"AXWindow", @"AXStandardWindow")]).count == 0, @"Ordinary windows are insufficient");
    NSMutableDictionary *missing = [sheet mutableCopy];
    missing[@"children"] = @[sheet[@"children"][0], sheet[@"children"][4]];
    NSCAssert(ADChromeApprovalButtons(@[missing]).count == 0, @"Title and button alone must not match");
    NSMutableDictionary *disabled = [sheet mutableCopy];
    NSMutableArray *children = [sheet[@"children"] mutableCopy];
    children[4] = @{@"role": @"AXButton", @"title": @"Allow", @"enabled": @NO};disabled[@"children"] = children;
    NSCAssert(ADChromeApprovalButtons(@[disabled]).count == 0, @"Disabled approval is not actionable");
    NSCAssert(ADChromeApprovalButtons(@[sheet, sheet]).count == 2, @"Ambiguous dialogs must remain distinguishable");
}

// A website cannot turn an imitation permission dialog into a native approval candidate.
static void webAndAmbiguityChecks(void) {
    NSDictionary *fake = @{@"role": @"AXWebArea", @"children": @[prompt(@"AXDialog", @"")]};
    NSCAssert(ADChromeApprovalButtons(@[fake]).count == 0, @"Web dialogs must be excluded");
    NSDictionary *wrapped = @{@"role": @"AXWindow", @"subrole": @"AXDialog", @"children": @[fake]};
    NSCAssert(ADChromeApprovalButtons(@[wrapped]).count == 0, @"Web text cannot supply native dialog identity");
    NSMutableDictionary *multiple = [prompt(@"AXSheet", @"") mutableCopy];
    multiple[@"children"] = [multiple[@"children"] arrayByAddingObject:@{@"role": @"AXButton", @"title": @"Allow", @"enabled": @YES}];
    NSCAssert(ADChromeApprovalButtons(@[multiple]).count == 0, @"Two Allow buttons are ambiguous");
    NSCAssert(ADChromeApprovalButtons(@[]).count == 0, @"No prompt means no action");
}

// Accept both observed Chrome versions without weakening punctuation or other prompt wording.
static void warningVariantChecks(void) {
    NSMutableDictionary *sheet = [prompt(@"AXSheet", @"") mutableCopy];
    NSMutableArray *children = [sheet[@"children"] mutableCopy];
    children[2] = @{@"role": @"AXStaticText", @"value": @"Only web developers should turn on this feature and only use it with trusted apps."};
    sheet[@"children"] = children;
    NSCAssert(ADChromeApprovalButtons(@[sheet]).count == 1, @"Expected observed current Chrome warning");
    children[2] = @{@"role": @"AXStaticText", @"value": @"Only web developers should turn on this feature; and only use it with trusted apps."};
    NSCAssert(ADChromeApprovalButtons(@[sheet]).count == 0, @"Unobserved punctuation changes must not match");
    children[2] = @{@"role": @"AXStaticText", @"value": @"Only web developers should turn on this feature and use it with any app."};
    NSCAssert(ADChromeApprovalButtons(@[sheet]).count == 0, @"Different warning text must not match");
}

int main(void) {
    @autoreleasepool { exactMatchChecks();webAndAmbiguityChecks();warningVariantChecks();puts("Chrome native matcher passed: both observed warning variants, exact prompts, webpage exclusion, disabled and ambiguous controls."); }
    return 0;
}
