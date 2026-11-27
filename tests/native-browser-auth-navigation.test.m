#import "../native/native-browser.h"

// Fixtures represent only observed controls; no credentials or native events are read or posted.
static NSDictionary *chooser(NSDictionary *button, NSArray *fields) {
    NSMutableArray *controls = [NSMutableArray arrayWithObject:button];
    [controls addObjectsFromArray:fields];
    return @{@"controls":controls,@"controls_truncated":@NO,@"native_modal":@NO};
}

// Choosing email is navigation only before any visible web input exists; toolbar inputs do not count.
static void emailChoiceOpensFields(void) {
    ADNativeBrowserState *state = [ADNativeBrowserState new];NSError *error = nil;
    NSDictionary *address = @{@"role":@"AXTextField",@"web_content":@NO,@"label":@"Address and search bar"};
    for (NSString *label in @[@"Sign in with email",@"Sign-in with e-mail",@"Login using e-mail",@"Log in via email address",@"Create an account using email"]) {
        NSDictionary *button = @{@"id":@"email",@"label":label,@"role":@"AXButton",@"web_content":@YES};
        state.observation = chooser(button,@[address]);
        NSCAssert(ADNativeAuthorizeEvent(state,@{@"action":@"click"},button,&error),@"Email method chooser may reveal credentials");
        NSCAssert(ADNativeAuthorizeEvent(state,@{@"action":@"key",@"key":@"return"},button,&error),@"Focused method choice has same navigation meaning");
        for (NSDictionary *field in @[@{@"role":@"AXTextField",@"web_content":@YES},@{@"role":@"AXTextField",@"web_content":@YES,@"secure":@YES}]) {
            state.observation = chooser(button,@[field]);
            NSCAssert(!ADNativeAuthorizeEvent(state,@{@"action":@"click"},button,&error),@"Email or password forms require private authentication");
            NSCAssert(ADNativeAuthorizeEvent(state,@{@"action":@"click",@"final_permit":@YES},button,&error),@"Bound private authentication remains available");
        }
    }
}

// Social-provider choices, ambiguous account buttons and final application actions stay protected.
static void preserveProtectedActions(void) {
    ADNativeBrowserState *state = [ADNativeBrowserState new];NSError *error = nil;
    for (NSString *label in @[@"Sign in with Google",@"Sign in with LinkedIn",@"Sign in",@"Create account",@"Submit application",@"Accept offer",@"Submit and sign in with email"]) {
        NSDictionary *button = @{@"label":label,@"role":@"AXButton",@"web_content":@YES};state.observation = chooser(button,@[]);
        NSCAssert(!ADNativeAuthorizeEvent(state,@{@"action":@"click"},button,&error),@"Protected action cannot masquerade as email navigation");
    }
    NSDictionary *field = @{@"label":@"Email",@"role":@"AXTextField",@"web_content":@YES};state.observation = chooser(field,@[]);
    NSCAssert(!ADNativeAuthorizeEvent(state,@{@"action":@"key",@"key":@"return"},field,&error),@"Implicit form Enter remains protected");
}

// Missing or truncated control evidence cannot establish that there is no account data to submit.
static void incompleteChoicesStayProtected(void) {
    ADNativeBrowserState *state = [ADNativeBrowserState new];NSError *error = nil;
    NSDictionary *button = @{@"label":@"Sign in with email",@"role":@"AXButton",@"web_content":@YES};
    NSCAssert(!ADNativeAuthorizeEvent(state,@{@"action":@"click"},button,&error),@"Missing observation rejected");
    NSMutableDictionary *page = [chooser(button,@[]) mutableCopy];page[@"controls_truncated"] = @YES;state.observation = page;
    NSCAssert(!ADNativeAuthorizeEvent(state,@{@"action":@"click"},button,&error),@"Incomplete control list rejected");
    page[@"controls_truncated"] = @NO;page[@"native_modal"] = @YES;
    NSCAssert(!ADNativeAuthorizeEvent(state,@{@"action":@"click"},button,&error),@"Native account dialogs cannot use webpage navigation exception");
}

int main(void) {
    @autoreleasepool { emailChoiceOpensFields();preserveProtectedActions();incompleteChoicesStayProtected();puts("Email method navigation, private credential boundaries, social/final protection and incomplete evidence checks passed."); }
    return 0;
}
