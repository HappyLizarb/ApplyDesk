#import "../native-browser.h"

// Visible page inputs cannot impersonate the native address bar or supply page identity.
static void addressIdentity(void) {
    NSMutableDictionary *native = [@{@"role":@"AXTextField",@"label":@"Address and search bar",
        @"value":@"https://employer.test/apply",@"web_content":@NO} mutableCopy];
    NSDictionary *web = @{@"role":@"AXTextField",@"label":@"Address and search bar",
        @"value":@"https://unrelated.test/",@"web_content":@YES};
    NSMutableDictionary *page = [@{@"controls":@[native,web],@"tabs":@[]} mutableCopy];
    ADNativeSummarizeObservation(page);
    NSCAssert([page[@"url"] isEqual:@"https://employer.test/apply"],@"Only native address supplies URL");
    NSCAssert(![page[@"text"] containsString:@"employer.test"],@"Native toolbar stays outside page text");
    NSMutableDictionary *onlyWeb = [@{@"controls":@[web],@"tabs":@[]} mutableCopy];
    ADNativeSummarizeObservation(onlyWeb);
    NSCAssert(onlyWeb[@"url"]==nil,@"Web-only field cannot provide browser URL");
    native[@"value"] = @"employer.test/apply";
    NSMutableDictionary *noScheme = [@{@"controls":@[native],@"tabs":@[]} mutableCopy];
    ADNativeSummarizeObservation(noScheme);
    NSCAssert(noScheme[@"url"]==nil,@"Scheme is never inferred");
}

// Empty titles use a native description without treating numbers or missing attributes as text.
static void visibleLabels(void) {
    NSCAssert([ADNativeControlLabel(@"",@"Address and search bar") isEqual:@"Address and search bar"],@"Empty title falls back");
    NSCAssert([ADNativeControlLabel(@"School",@"Type school") isEqual:@"School"],@"Nonempty title retains precedence");
    NSCAssert([ADNativeControlLabel(nil,@"Website") isEqual:@"Website"],@"Missing title uses description");
    NSCAssert([ADNativeControlLabel(@42,NSNull.null) isEqual:@""],@"Nontext metadata does not become a label");
}

// Native web-area identity takes precedence over an address the user has typed but not navigated to.
static void nativePageURL(void) {
    NSString *actual = @"https://example.com/";
    NSCAssert([ADNativeHTTPURL([NSURL URLWithString:actual]) isEqual:actual],@"Actual captured AXWebArea NSURL is authoritative");
    NSCAssert([ADNativeHTTPURL(actual) isEqual:actual],@"Native NSString URL is preserved verbatim");
    for (NSString *invalid in @[@"example.com",@"chrome://newtab",@"https://user:password@example.com/",@"https://@example.com/",@"https:///missing-host"])
        NSCAssert(ADNativeHTTPURL(invalid)==nil,@"Only complete HTTP URLs without credentials are accepted");
    NSDictionary *address = @{@"role":@"AXTextField",@"label":@"Address and search bar",@"web_content":@NO,
        @"focused":@YES,@"value":@"https://typed-but-not-visited.test/"};
    NSMutableDictionary *page = [@{@"url":actual,@"controls":@[address],@"tabs":@[]} mutableCopy];
    ADNativeSummarizeObservation(page);
    NSCAssert([page[@"url"] isEqual:actual],@"Focused omnibox cannot overwrite actual visible page identity");
}

int main(void) {
    @autoreleasepool { addressIdentity();visibleLabels();nativePageURL();puts("Native page URL precedence, credential rejection, visible-label fallback and no inferred scheme checks passed."); }
    return 0;
}
