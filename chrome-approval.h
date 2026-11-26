#import <Cocoa/Cocoa.h>

// Matching is pure so synthetic trees can verify that webpage buttons are never candidates.
NSArray<NSDictionary *> *ADChromeApprovalButtons(NSArray<NSDictionary *> *windows);
int ADApproveChromeConnection(NSString *endpoint);
