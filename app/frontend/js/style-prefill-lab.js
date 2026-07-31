(() => {
  const form = document.getElementById('style-prefill-form');
  const imageInput = document.getElementById('image');
  const previewImage = document.getElementById('preview-image');
  const previewEmpty = document.getElementById('preview-empty');
  const statusPanel = document.getElementById('status');
  const structuredOutput = document.getElementById('structured-output');
  const rawOutput = document.getElementById('raw-output');
  const analyzeButton = document.getElementById('analyze-btn');
  const apiModeSelect = document.getElementById('api-mode');
  const endpointUrlInput = document.getElementById('endpoint-url');
  const apiKeyInput = document.getElementById('api-key');
  const mcpToolsetRefreshButton = document.getElementById('mcp-toolset-refresh-btn');
  const mcpToolsetSelect = document.getElementById('mcp-toolset-select');
  const mcpToolsetDetails = document.getElementById('mcp-toolset-details');
  const mcpServersHeaderInput = document.getElementById('mcp-servers-header');
  const mcpServerUrlInput = document.getElementById('mcp-server-url');
  const mcpTargetModeSelect = document.getElementById('mcp-target-mode');
  const probeMcpImageUrlButton = document.getElementById('probe-mcp-image-url-btn');
  const mcpProbeOutput = document.getElementById('mcp-probe-output');

  if (!form || !imageInput || !previewImage || !previewEmpty || !statusPanel || !structuredOutput || !rawOutput || !analyzeButton) {
    return;
  }

  const setStatus = (state, message) => {
    statusPanel.classList.remove('is-idle', 'is-working', 'is-ok', 'is-error');
    statusPanel.classList.add(state);
    statusPanel.textContent = message;
  };

  imageInput.addEventListener('change', () => {
    const file = imageInput.files && imageInput.files[0] ? imageInput.files[0] : null;
    if (!file) {
      previewImage.hidden = true;
      previewEmpty.hidden = false;
      previewImage.src = '';
      return;
    }

    const url = URL.createObjectURL(file);
    previewImage.src = url;
    previewImage.hidden = false;
    previewEmpty.hidden = true;
  });

  const renderToolsetOptionLabel = (toolset) => {
    const name = toolset.toolset_name || toolset.toolset_id || 'unnamed';
    const count = Array.isArray(toolset.tools) ? toolset.tools.length : 0;
    return `${name} (${count} tools)`;
  };

  const getOriginFromUrl = (value) => {
    if (!value) {
      return '';
    }
    try {
      return new URL(value).origin;
    } catch {
      return '';
    }
  };

  const deriveToolsetMcpUrl = (toolsetName) => {
    if (!toolsetName) {
      return '';
    }
    const endpointValue = endpointUrlInput && endpointUrlInput.value ? endpointUrlInput.value.trim() : '';
    const endpointOrigin = getOriginFromUrl(endpointValue);

    const discoveredBase = mcpToolsetSelect && mcpToolsetSelect.dataset.endpointBaseUrl
      ? mcpToolsetSelect.dataset.endpointBaseUrl.trim()
      : '';
    const discoveredOrigin = getOriginFromUrl(discoveredBase);

    const currentMcpOrigin = mcpServerUrlInput && mcpServerUrlInput.value
      ? getOriginFromUrl(mcpServerUrlInput.value.trim())
      : '';

    const origin = endpointOrigin || discoveredOrigin || currentMcpOrigin;
    if (!origin) {
      return '';
    }

    return `${origin}/toolset/${encodeURIComponent(toolsetName)}/mcp`;
  };

  const loadMcpToolsets = async () => {
    if (!mcpToolsetRefreshButton || !mcpToolsetSelect || !mcpToolsetDetails) {
      return;
    }

    mcpToolsetRefreshButton.disabled = true;
    mcpToolsetDetails.textContent = 'Loading toolsets...';

    try {
      const params = new URLSearchParams();
      const endpointValue = endpointUrlInput && endpointUrlInput.value ? endpointUrlInput.value.trim() : '';
      const apiKeyValue = apiKeyInput && apiKeyInput.value ? apiKeyInput.value.trim() : '';
      if (endpointValue) {
        params.set('endpoint_base_url', endpointValue);
      }
      if (apiKeyValue) {
        params.set('api_key', apiKeyValue);
      }

      const query = params.toString();
      const response = await fetch(query ? `/api/style-prefill/mcp-toolsets?${query}` : '/api/style-prefill/mcp-toolsets');
      const data = await response.json();
      if (!response.ok || !data || !Array.isArray(data.toolsets)) {
        mcpToolsetDetails.textContent = 'Failed to load toolsets.';
        if (data) {
          rawOutput.textContent = JSON.stringify(data, null, 2);
        }
        return;
      }

      mcpToolsetSelect.innerHTML = '<option value="">Select discovered toolset</option>';
      for (const toolset of data.toolsets) {
        const option = document.createElement('option');
        option.value = String(toolset.toolset_name || toolset.toolset_id || '');
        option.textContent = renderToolsetOptionLabel(toolset);
        option.dataset.tools = JSON.stringify(Array.isArray(toolset.tools) ? toolset.tools : []);
        mcpToolsetSelect.appendChild(option);
      }

      mcpToolsetSelect.dataset.endpointBaseUrl = typeof data.endpoint_base_url === 'string'
        ? data.endpoint_base_url
        : '';

      mcpToolsetDetails.textContent = `Loaded ${data.toolsets.length} toolset(s) from ${data.endpoint_base_url}.`;
    } catch (error) {
      mcpToolsetDetails.textContent = 'Error loading MCP toolsets.';
      rawOutput.textContent = error instanceof Error ? error.message : String(error);
    } finally {
      mcpToolsetRefreshButton.disabled = false;
    }
  };

  if (mcpToolsetRefreshButton) {
    mcpToolsetRefreshButton.addEventListener('click', loadMcpToolsets);
  }

  if (mcpToolsetSelect) {
    mcpToolsetSelect.addEventListener('change', () => {
      const selected = mcpToolsetSelect.value;

      const option = mcpToolsetSelect.selectedOptions && mcpToolsetSelect.selectedOptions[0]
        ? mcpToolsetSelect.selectedOptions[0]
        : null;
      if (!option || !mcpToolsetDetails) {
        return;
      }

      if (!selected) {
        mcpToolsetDetails.textContent = 'Load toolsets to populate this list from LiteLLM /mcp/toolset.';
        return;
      }

      // Selecting a toolset implies toolset MCP routing; switch mode automatically
      // and populate the URL so users don't need an extra manual step.
      if (mcpTargetModeSelect) {
        mcpTargetModeSelect.value = 'toolset';
      }
      if (mcpServerUrlInput) {
        const derived = deriveToolsetMcpUrl(selected);
        if (derived) {
          mcpServerUrlInput.value = derived;
        }
      }

      try {
        const tools = JSON.parse(option.dataset.tools || '[]');
        const names = Array.isArray(tools) ? tools.map((t) => t && t.tool_name ? t.tool_name : '').filter(Boolean) : [];
        const modeHint = (mcpTargetModeSelect && mcpTargetModeSelect.value === 'toolset')
          ? 'Using toolset MCP URL; x-mcp-servers can be blank.'
          : 'Keep x-mcp-servers set to your MCP server label (e.g. zai_vision_mcp).';
        mcpToolsetDetails.textContent = names.length > 0
          ? `Selected ${selected}: ${names.join(', ')}. ${modeHint}`
          : `Selected ${selected}. ${modeHint}`;
      } catch {
        const modeHint = (mcpTargetModeSelect && mcpTargetModeSelect.value === 'toolset')
          ? 'Using toolset MCP URL; x-mcp-servers can be blank.'
          : 'Keep x-mcp-servers set to your MCP server label (e.g. zai_vision_mcp).';
        mcpToolsetDetails.textContent = `Selected ${selected}. ${modeHint}`;
      }
    });
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();

    const file = imageInput.files && imageInput.files[0] ? imageInput.files[0] : null;
    if (!file) {
      setStatus('is-error', 'Select an image before running analysis.');
      return;
    }

    const payload = new FormData(form);
    analyzeButton.disabled = true;
    setStatus('is-working', 'Submitting image to analysis endpoint...');
    structuredOutput.textContent = '{}';
    rawOutput.textContent = '';

    try {
      const endpoint = (apiModeSelect && apiModeSelect.value === 'responses-mcp')
        ? '/api/style-prefill/analyze-single-responses-mcp'
        : '/api/style-prefill/analyze-single';

      const response = await fetch(endpoint, {
        method: 'POST',
        body: payload,
      });

      const data = await response.json();
      if (!response.ok) {
        const detail = data && data.detail ? data.detail : data;
        setStatus('is-error', 'Analysis failed. Check raw output for details.');
        structuredOutput.textContent = JSON.stringify(detail, null, 2);
        rawOutput.textContent = JSON.stringify(data, null, 2);
        return;
      }

      setStatus('is-ok', 'Analysis complete.');
      structuredOutput.textContent = JSON.stringify(data.structured || {}, null, 2);
      rawOutput.textContent = JSON.stringify(
        {
          request: data.request || null,
          completion_meta: data.completion_meta || null,
          raw_completion_text: data.raw_completion_text || '',
        },
        null,
        2,
      );
    } catch (error) {
      setStatus('is-error', 'Network or server error while running analysis.');
      rawOutput.textContent = error instanceof Error ? error.message : String(error);
    } finally {
      analyzeButton.disabled = false;
    }
  });

  if (probeMcpImageUrlButton) {
    probeMcpImageUrlButton.addEventListener('click', async () => {
      const file = imageInput.files && imageInput.files[0] ? imageInput.files[0] : null;
      if (!file) {
        setStatus('is-error', 'Select an image before running MCP URL probe.');
        return;
      }

      const payload = new FormData(form);
      probeMcpImageUrlButton.disabled = true;
      setStatus('is-working', 'Running MCP image URL reachability probe...');
      if (mcpProbeOutput) {
        mcpProbeOutput.textContent = '';
      }

      try {
        const response = await fetch('/api/style-prefill/mcp-probe-image-url', {
          method: 'POST',
          body: payload,
        });

        const data = await response.json();
        if (!response.ok) {
          setStatus('is-error', 'MCP URL probe failed. See probe output.');
          if (mcpProbeOutput) {
            mcpProbeOutput.textContent = JSON.stringify(data && data.detail ? data.detail : data, null, 2);
          }
          return;
        }

        const reachable = !!(data && data.probe && data.probe.reachable);
        setStatus(
          reachable ? 'is-ok' : 'is-error',
          reachable
            ? 'MCP URL probe succeeded: MCP fetched the hosted image URL.'
            : 'MCP URL probe completed but did not fetch the hosted image URL.',
        );
        if (mcpProbeOutput) {
          mcpProbeOutput.textContent = JSON.stringify(data, null, 2);
        }
      } catch (error) {
        setStatus('is-error', 'Network/server error during MCP URL probe.');
        if (mcpProbeOutput) {
          mcpProbeOutput.textContent = error instanceof Error ? error.message : String(error);
        }
      } finally {
        probeMcpImageUrlButton.disabled = false;
      }
    });
  }
})();
