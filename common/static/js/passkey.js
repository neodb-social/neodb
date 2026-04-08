// WebAuthn / Passkey support for NeoDB

(function () {
  "use strict";

  function base64urlDecode(str) {
    str = str.replace(/-/g, "+").replace(/_/g, "/");
    while (str.length % 4) str += "=";
    var binary = atob(str);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
  }

  function base64urlEncode(buffer) {
    var bytes = new Uint8Array(buffer);
    var binary = "";
    for (var i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function getCsrfToken() {
    var el = document.querySelector("[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  async function postJSON(url, data) {
    var resp = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      body: JSON.stringify(data),
    });
    return resp.json();
  }

  async function passkeyRegister(optionsUrl, verifyUrl, onSuccess, onError) {
    try {
      // 1. Get registration options from server
      var optResp = await fetch(optionsUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCsrfToken(),
          "Content-Type": "application/json",
        },
        body: "{}",
      });
      if (!optResp.ok) {
        throw new Error("Failed to get registration options");
      }
      var options = await optResp.json();

      // 2. Convert base64url fields to ArrayBuffer
      options.challenge = base64urlDecode(options.challenge);
      options.user.id = base64urlDecode(options.user.id);
      if (options.excludeCredentials) {
        options.excludeCredentials = options.excludeCredentials.map(function (c) {
          return Object.assign({}, c, { id: base64urlDecode(c.id) });
        });
      }

      // 3. Call WebAuthn API
      var credential = await navigator.credentials.create({ publicKey: options });

      // 4. Encode response for server
      var response = credential.response;
      var body = {
        id: credential.id,
        rawId: base64urlEncode(credential.rawId),
        type: credential.type,
        response: {
          attestationObject: base64urlEncode(response.attestationObject),
          clientDataJSON: base64urlEncode(response.clientDataJSON),
        },
        transports: response.getTransports ? response.getTransports() : [],
      };

      // 5. Ask user for a friendly name
      var name = prompt(
        document.documentElement.lang.startsWith("zh")
          ? "为这个通行密钥取个名字:"
          : "Name this passkey:",
        "Passkey"
      );
      if (name) body.name = name;

      // 6. Verify with server
      var result = await postJSON(verifyUrl, body);
      if (result.ok) {
        if (onSuccess) onSuccess(result);
      } else {
        throw new Error(result.error || "Registration failed");
      }
    } catch (e) {
      if (e.name === "NotAllowedError") {
        // User cancelled
        if (onError) onError(null);
      } else {
        if (onError) onError(e.message || String(e));
      }
    }
  }

  async function passkeyLogin(optionsUrl, verifyUrl, onSuccess, onError) {
    try {
      // 1. Get authentication options from server
      var optResp = await fetch(optionsUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCsrfToken(),
          "Content-Type": "application/json",
        },
        body: "{}",
      });
      if (!optResp.ok) {
        throw new Error("Failed to get login options");
      }
      var options = await optResp.json();

      // 2. Convert base64url fields
      options.challenge = base64urlDecode(options.challenge);
      if (options.allowCredentials) {
        options.allowCredentials = options.allowCredentials.map(function (c) {
          return Object.assign({}, c, { id: base64urlDecode(c.id) });
        });
      }

      // 3. Call WebAuthn API
      var assertion = await navigator.credentials.get({ publicKey: options });

      // 4. Encode response for server
      var response = assertion.response;
      var body = {
        id: assertion.id,
        rawId: base64urlEncode(assertion.rawId),
        type: assertion.type,
        response: {
          authenticatorData: base64urlEncode(response.authenticatorData),
          clientDataJSON: base64urlEncode(response.clientDataJSON),
          signature: base64urlEncode(response.signature),
          userHandle: response.userHandle
            ? base64urlEncode(response.userHandle)
            : null,
        },
      };

      // 5. Verify with server
      var result = await postJSON(verifyUrl, body);
      if (result.ok) {
        if (onSuccess) onSuccess(result);
      } else {
        throw new Error(result.error || "Login failed");
      }
    } catch (e) {
      if (e.name === "NotAllowedError") {
        if (onError) onError(null);
      } else {
        if (onError) onError(e.message || String(e));
      }
    }
  }

  async function passkeyDelete(url, id, onSuccess, onError) {
    try {
      var result = await postJSON(url, { id: id });
      if (result.ok) {
        if (onSuccess) onSuccess(result);
      } else {
        throw new Error(result.error || "Delete failed");
      }
    } catch (e) {
      if (onError) onError(e.message || String(e));
    }
  }

  async function passkeyRename(url, id, name, onSuccess, onError) {
    try {
      var result = await postJSON(url, { id: id, name: name });
      if (result.ok) {
        if (onSuccess) onSuccess(result);
      } else {
        throw new Error(result.error || "Rename failed");
      }
    } catch (e) {
      if (onError) onError(e.message || String(e));
    }
  }

  // Expose to global scope
  window.Passkey = {
    register: passkeyRegister,
    login: passkeyLogin,
    remove: passkeyDelete,
    rename: passkeyRename,
    isSupported: function () {
      return !!window.PublicKeyCredential;
    },
  };
})();
