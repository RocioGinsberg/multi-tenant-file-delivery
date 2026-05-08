(function (global) {
  async function _readBody(response) {
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      try {
        return await response.json();
      } catch (_) {
        return {};
      }
    }
    const text = await response.text().catch(() => '');
    return {detail: text};
  }

  function _buildError(response, payload, fallbackMessage) {
    const err = new Error(
      payload?.detail ||
      payload?.message ||
      fallbackMessage ||
      `HTTP ${response.status}`,
    );
    err.status = response.status;
    err.payload = payload || {};
    err.detail = payload?.detail || payload?.message || '';
    return err;
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const payload = await _readBody(response);
    if (!response.ok) {
      throw _buildError(response, payload, `HTTP ${response.status}`);
    }
    return payload;
  }

  async function getJson(url, options) {
    return requestJson(url, options);
  }

  async function requestBlob(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
      const payload = await _readBody(response);
      throw _buildError(response, payload, `HTTP ${response.status}`);
    }
    return response;
  }

  async function sendJson(url, options) {
    const body = options && Object.prototype.hasOwnProperty.call(options, 'body')
      ? options.body
      : undefined;
    return requestJson(url, {
      ...(options || {}),
      headers: {
        'Content-Type': 'application/json',
        ...((options && options.headers) || {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  async function sendForm(url, formData, options) {
    return requestJson(url, {
      ...(options || {}),
      body: formData,
    });
  }

  function errorMessage(error, fallbackMessage) {
    if (!error) return fallbackMessage || '请求失败';
    return error.detail || error.message || fallbackMessage || '请求失败';
  }

  global.PortalApi = {
    requestJson,
    getJson,
    requestBlob,
    sendJson,
    sendForm,
    errorMessage,
  };
})(window);
