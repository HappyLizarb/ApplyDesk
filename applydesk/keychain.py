"""Use macOS Keychain APIs directly so secrets never appear in command arguments."""

import ctypes as c
import sys


class Keychain:
    """Store only generic passwords under an app-specific service."""

    def __init__(self, service="local.applydesk"):
        if sys.platform != "darwin":
            raise RuntimeError("ApplyDesk requires macOS Keychain.")
        self.service = service.encode()
        self.sec = c.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.cf = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.cf.CFRelease.argtypes = [c.c_void_p]
        self.sec.SecKeychainFindGenericPassword.argtypes = [c.c_void_p, c.c_uint32, c.c_char_p, c.c_uint32, c.c_char_p, c.POINTER(c.c_uint32), c.POINTER(c.c_void_p), c.POINTER(c.c_void_p)]
        self.sec.SecKeychainAddGenericPassword.argtypes = [c.c_void_p, c.c_uint32, c.c_char_p, c.c_uint32, c.c_char_p, c.c_uint32, c.c_void_p, c.POINTER(c.c_void_p)]
        self.sec.SecKeychainItemModifyAttributesAndData.argtypes = [c.c_void_p, c.c_void_p, c.c_uint32, c.c_void_p]
        self.sec.SecKeychainItemFreeContent.argtypes = [c.c_void_p, c.c_void_p]
        self.sec.SecKeychainItemDelete.argtypes = [c.c_void_p]

    def _find(self, account):
        """Return a Keychain item and data pointer; caller must release both."""
        name = account.encode()
        length, data, item = c.c_uint32(), c.c_void_p(), c.c_void_p()
        status = self.sec.SecKeychainFindGenericPassword(None, len(self.service), self.service, len(name), name, c.byref(length), c.byref(data), c.byref(item))
        if status not in (0, -25300):
            raise RuntimeError(f"Keychain access failed ({status}). Unlock your login Keychain and retry.")
        return status, length.value, data, item

    def get(self, account):
        """Read a secret into memory without exposing it in the API response."""
        status, length, data, item = self._find(account)
        if status == -25300:
            return None
        try:
            return c.string_at(data, length).decode()
        finally:
            self.sec.SecKeychainItemFreeContent(None, data)
            self.cf.CFRelease(item)

    def set(self, account, secret):
        """Update or remove a secret, retaining the platform's access controls."""
        status, _, data, item = self._find(account)
        name, value = account.encode(), secret.encode()
        try:
            if status == 0:
                self.sec.SecKeychainItemFreeContent(None, data)
                result = self.sec.SecKeychainItemModifyAttributesAndData(item, None, len(value), value) if secret else self.sec.SecKeychainItemDelete(item)
            elif secret:
                result = self.sec.SecKeychainAddGenericPassword(None, len(self.service), self.service, len(name), name, len(value), value, None)
            else:
                return
            if result:
                raise RuntimeError(f"Keychain save failed ({result}).")
        finally:
            if item:
                self.cf.CFRelease(item)
