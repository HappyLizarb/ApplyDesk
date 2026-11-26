#import "native-browser-capture.h"
#import <AppKit/AppKit.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#include <math.h>

// Errors describe the required recovery without logging window titles or captured content.
static NSError *captureError(NSInteger code, NSString *message) {
    return [NSError errorWithDomain:@"ApplyDesk.NativeCapture" code:code
        userInfo:@{NSLocalizedDescriptionKey: message}];
}

// The exact window must still belong to the expected process and occupy its observed screen bounds.
static BOOL matchingWindow(pid_t pid, CGWindowID windowID, CGRect expectedBounds) {
    NSArray *windows = CFBridgingRelease(CGWindowListCopyWindowInfo(kCGWindowListOptionIncludingWindow, windowID));
    if (windows.count != 1) return NO;
    NSDictionary *window = windows.firstObject;
    CGRect bounds;
    return [window[(__bridge NSString *)kCGWindowNumber] unsignedIntValue] == windowID
        && [window[(__bridge NSString *)kCGWindowOwnerPID] intValue] == pid
        && [window[(__bridge NSString *)kCGWindowIsOnscreen] boolValue]
        && CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)window[(__bridge NSString *)kCGWindowBounds], &bounds)
        && CGRectEqualToRect(bounds, expectedBounds);
}

// Pump the caller's run loop while bounding asynchronous ScreenCaptureKit callbacks to five seconds.
static BOOL completedCapture(dispatch_semaphore_t completed) {
    NSTimeInterval deadline = NSProcessInfo.processInfo.systemUptime + 5;
    do {
        if (dispatch_semaphore_wait(completed, DISPATCH_TIME_NOW) == 0) return YES;
        [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
    } while (NSProcessInfo.processInfo.systemUptime < deadline);
    return dispatch_semaphore_wait(completed, DISPATCH_TIME_NOW) == 0;
}

// Enumerate metadata only; the eventual content filter includes exactly one matching window.
static SCWindow *shareableWindow(pid_t pid, CGWindowID windowID, CGRect bounds, NSError **error) {
    dispatch_semaphore_t completed = dispatch_semaphore_create(0);
    __block SCWindow *selected = nil;
    __block NSError *failure = nil;
    [SCShareableContent getShareableContentExcludingDesktopWindows:YES onScreenWindowsOnly:YES
        completionHandler:^(SCShareableContent *content, NSError *problem) {
            failure = problem;
            for (SCWindow *window in content.windows) {
                if (window.windowID == windowID && window.owningApplication.processID == pid
                    && window.isOnScreen && CGRectEqualToRect(window.frame, bounds)) {
                    selected = window;
                    break;
                }
            }
            dispatch_semaphore_signal(completed);
        }];
    if (!completedCapture(completed)) {
        if (error) *error = captureError(3, @"The application-window screenshot timed out.");
        return nil;
    }
    if (!selected && error) *error = failure ?: captureError(4, @"The application window changed. Observe it again.");
    return selected;
}

// Encode only the filtered window image, preserving the point-to-pixel mapping for native input.
static NSDictionary *encodeWindow(CGImageRef image, CGRect bounds, NSError **error) {
    if (!image) {
        if (error) *error = captureError(5, @"The application-window screenshot was unavailable.");
        return nil;
    }
    NSData *png = [[[NSBitmapImageRep alloc] initWithCGImage:image]
        representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
    if (!png.length || png.length > 20 * 1024 * 1024) {
        if (error) *error = captureError(6, @"The application-window screenshot could not be encoded within its size limit.");
        return nil;
    }
    return @{@"mime_type": @"image/png", @"data": [png base64EncodedStringWithOptions:0],
        @"width": @(CGImageGetWidth(image)), @"height": @(CGImageGetHeight(image)),
        @"scale": @((double)CGImageGetWidth(image) / bounds.size.width),
        @"bounds": @{@"x": @(bounds.origin.x), @"y": @(bounds.origin.y),
                     @"width": @(bounds.size.width), @"height": @(bounds.size.height)}};
}

// Capture the one authorized window at its observed scale without including its child windows.
static NSDictionary *captureFilter(SCContentFilter *filter, CGRect bounds, NSError **error) API_AVAILABLE(macos(14.0));
static NSDictionary *captureFilter(SCContentFilter *filter, CGRect bounds, NSError **error) {
    SCStreamConfiguration *config = [SCStreamConfiguration new];
    double scale = MIN(MAX(filter.pointPixelScale, 1), 4096.0 / MAX(bounds.size.width, bounds.size.height));
    config.width = (size_t)ceil(bounds.size.width * scale);
    config.height = (size_t)ceil(bounds.size.height * scale);
    config.showsCursor = NO;
    config.ignoreShadowsSingleWindow = YES;
    if (@available(macOS 14.2, *)) config.includeChildWindows = NO;
    dispatch_semaphore_t completed = dispatch_semaphore_create(0);
    __block NSDictionary *result = nil;
    __block NSError *failure = nil;
    [SCScreenshotManager captureImageWithFilter:filter configuration:config completionHandler:^(CGImageRef image, NSError *problem) {
        failure = problem;
        if (!failure) result = encodeWindow(image, bounds, &failure);
        dispatch_semaphore_signal(completed);
    }];
    if (!completedCapture(completed)) {
        if (error) *error = captureError(3, @"The application-window screenshot timed out.");
        return nil;
    }
    if (!result && error) *error = failure ?: captureError(5, @"The application-window screenshot was unavailable.");
    return result;
}

// Permission is checked without requesting it, and window ownership/bounds are checked before and after capture.
NSDictionary *ADNativeCaptureWindow(pid_t pid, CGWindowID windowID, CGRect expectedBounds, NSError **error) {
    if (error) *error = nil;
    BOOL valid = pid > 0 && windowID != kCGNullWindowID && !CGRectIsNull(expectedBounds)
        && !CGRectIsInfinite(expectedBounds) && expectedBounds.size.width > 0 && expectedBounds.size.height > 0
        && isfinite(expectedBounds.origin.x) && isfinite(expectedBounds.origin.y)
        && isfinite(expectedBounds.size.width) && isfinite(expectedBounds.size.height);
    if (!valid) {
        if (error) *error = captureError(1, @"An exact application window and its observed bounds are required.");
        return nil;
    }
    if (!CGPreflightScreenCaptureAccess()) {
        if (error) *error = captureError(2, @"Enable Screen Recording for ApplyDesk to inspect application-window screenshots.");
        return nil;
    }
    if (!matchingWindow(pid, windowID, expectedBounds)) {
        if (error) *error = captureError(4, @"The application window changed. Observe it again.");
        return nil;
    }
    if (@available(macOS 14.0, *)) {
        SCWindow *window = shareableWindow(pid, windowID, expectedBounds, error);
        if (!window) return nil;
        SCContentFilter *filter = [[SCContentFilter alloc] initWithDesktopIndependentWindow:window];
        NSDictionary *result = captureFilter(filter, expectedBounds, error);
        if (result && !matchingWindow(pid, windowID, expectedBounds)) {
            if (error) *error = captureError(4, @"The application window changed during its screenshot. Observe it again.");
            return nil;
        }
        return result;
    }
    if (error) *error = captureError(7, @"Application-window screenshots require macOS 14 or later.");
    return nil;
}
