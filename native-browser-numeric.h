#import "native-browser.h"

// A numeric wrapper is evidence of a visible native control, never permission to set its AX value.
BOOL ADNativeNumericWrapperMatches(NSDictionary *evidence, CGRect nativeBounds, CGRect wrapperBounds, CGRect clip);
NSDictionary *ADNativeNumericWrapper(ADNativeBrowserState *state, id object, CGRect clip);
