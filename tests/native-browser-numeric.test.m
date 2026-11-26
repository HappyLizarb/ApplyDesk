#import "../native-browser-numeric.h"
#import "../native-browser-input.h"

// Geometry and structure come from the two observed native year controls, without any applicant data.
static void numericWrapperEvidence(void) {
    NSDictionary *proof = @{@"role":@"AXIncrementor",@"wrapper_role":@"AXGroup",@"secure":@NO,
        @"enabled":@YES,@"value_settable":@YES,@"unique_child":@YES,@"exact_wrapper_hit":@YES,@"owned_wrapper":@YES};
    CGRect clip = CGRectMake(220,155,839,833), native = CGRectMake(393,193,1,0), wrapper = CGRectMake(389,180,34,28);
    NSCAssert(ADNativeNumericWrapperMatches(proof,native,wrapper,clip),@"Observed unique numeric wrapper qualifies");
    NSArray *invalid = @[@{@"role":@"AXTextField"},@{@"wrapper_role":@"AXWebArea"},@{@"secure":@YES},
        @{@"enabled":@NO},@{@"value_settable":@NO},@{@"unique_child":@NO},@{@"exact_wrapper_hit":@NO},@{@"owned_wrapper":@NO}];
    for (NSDictionary *changes in invalid) {
        NSMutableDictionary *candidate = [proof mutableCopy];[candidate addEntriesFromDictionary:changes];
        NSCAssert(!ADNativeNumericWrapperMatches(candidate,native,wrapper,clip),@"Unproved, hidden or unrelated controls cannot borrow geometry");
    }
    NSCAssert(!ADNativeNumericWrapperMatches(proof,CGRectMake(393,193,1,4),wrapper,clip),@"A visible native rectangle needs no projection");
    NSCAssert(!ADNativeNumericWrapperMatches(proof,native,CGRectZero,clip),@"An empty wrapper cannot be exposed");
    NSCAssert(!ADNativeNumericWrapperMatches(proof,native,CGRectMake(389,140,34,28),clip),@"A partially clipped wrapper is rejected");
    NSCAssert(!ADNativeNumericWrapperMatches(proof,native,CGRectMake(1200,180,34,28),clip),@"An offscreen wrapper is rejected");
    NSCAssert(!ADNativeNumericWrapperMatches(proof,CGRectMake(0,0,1,0),wrapper,clip),@"An unrelated hidden numeric anchor is rejected");
}

// The numeric role supports ordinary focus and typing while keeping implicit submission behind authority.
static void numericFinalAuthority(void) {
    ADNativeBrowserState *state = [ADNativeBrowserState new];NSError *error = nil;
    NSDictionary *year = @{@"role":@"AXIncrementor",@"label":@"Year",@"web_content":@YES};
    NSCAssert(ADNativeAuthorizeEvent(state,@{@"action":@"type"},year,&error),@"Typing is not a final action");
    NSCAssert(!ADNativeAuthorizeEvent(state,@{@"action":@"key",@"key":@"return"},year,&error),@"Numeric Return cannot implicitly submit");
    NSCAssert(ADNativeAuthorizeEvent(state,@{@"action":@"key",@"key":@"tab"},year,&error),@"Tab remains ordinary navigation");
    state.observation = @{@"controls":@[year]};
    NSDictionary *login = @{@"role":@"AXButton",@"label":@"Sign in with email",@"web_content":@YES};
    NSCAssert(!ADNativeAuthorizeEvent(state,@{@"action":@"click"},login,&error),@"A numeric field prevents classifying an account form as a method chooser");
}

int main(void) {
    @autoreleasepool {
        numericWrapperEvidence();numericFinalAuthority();
        puts("Native numeric wrapper proof, negative geometry/identity cases and final authority passed.");
    }
    return 0;
}
