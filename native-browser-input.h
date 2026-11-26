#import "native-browser.h"

// Pure key definitions let tests verify allowed native shortcuts without generating any input.
NSDictionary *ADNativeKeyDefinition(NSString *name);
BOOL ADNativeValidateObservation(NSDictionary *prior, NSDictionary *fresh, NSDictionary *params, NSError **error);
BOOL ADNativeAuthorizeEvent(ADNativeBrowserState *state, NSDictionary *params, NSDictionary *control, NSError **error);
BOOL ADNativeMove(ADNativeBrowserState *state, NSDictionary *params, NSError **error);
