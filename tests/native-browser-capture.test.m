#import "../native-browser-capture.h"
#include <assert.h>
#include <math.h>

// Invalid targets fail before permission checks, enumeration or screen capture; no personal window is touched.
int main(void) {
    @autoreleasepool {
        NSError *error = nil;
        assert(ADNativeCaptureWindow(0, 0, CGRectZero, &error) == nil);
        assert([error.domain isEqual:@"ApplyDesk.NativeCapture"] && error.code == 1);
        assert(ADNativeCaptureWindow(1, 1, CGRectMake(NAN, 0, 100, 100), &error) == nil);
        assert(error.code == 1);
        assert(ADNativeCaptureWindow(1, 1, CGRectInfinite, &error) == nil);
        assert(error.code == 1);
        assert(ADNativeCaptureWindow(1, 1, CGRectMake(0, 0, 0, 100), NULL) == nil);
        puts("Native capture SDK/link and invalid-target checks passed without requesting permission or capturing a window.");
    }
    return 0;
}
