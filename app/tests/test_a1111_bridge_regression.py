#!/usr/bin/env python3
"""Regression tests for A1111 bridge fallback Comfy workflow synthesis."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_APP_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_APP_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
	sys.path.insert(0, str(BACKEND_DIR))

import main as backend_main  # noqa: E402


def test_fallback_workflow_builder_handles_non_numeric_link_source() -> None:
	"""Invalid link-style inputs must not crash fallback workflow generation."""
	prompt_graph = {
		"1": {
			"class_type": "KSampler",
			"inputs": {
				"model": ["not-a-node-id", 0],
				"seed": 123,
				"steps": 20,
				"cfg": 7.0,
				"sampler_name": "euler",
				"scheduler": "normal",
				"denoise": 1.0,
			},
		}
	}

	workflow_payload, warnings = backend_main._build_fallback_comfy_workflow_ui_from_prompt_graph(prompt_graph)

	assert workflow_payload is not None
	assert isinstance(warnings, list)
	assert isinstance(workflow_payload.get("nodes"), list)
	assert isinstance(workflow_payload.get("links"), list)
	assert len(workflow_payload["nodes"]) == 1
	assert workflow_payload["last_node_id"] == 1
	assert workflow_payload["last_link_id"] == 0


def test_fallback_workflow_builder_keeps_valid_links() -> None:
	"""Valid prompt-graph links should still produce workflow links."""
	prompt_graph = {
		"1": {
			"class_type": "CheckpointLoaderSimple",
			"inputs": {
				"ckpt_name": "model.safetensors",
			},
		},
		"2": {
			"class_type": "CLIPTextEncode",
			"inputs": {
				"clip": [1, 1],
				"text": "hello",
			},
		},
	}

	workflow_payload, _warnings = backend_main._build_fallback_comfy_workflow_ui_from_prompt_graph(prompt_graph)

	assert workflow_payload is not None
	links = workflow_payload.get("links")
	assert isinstance(links, list)
	assert len(links) == 1
