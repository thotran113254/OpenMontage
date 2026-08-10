#!/usr/bin/env python3
"""Phase 05 Test: NineRouterImage registration and availability.

Validates:
  - Tool instantiation and name
  - Availability tied to NINE_ROUTER_API_KEY and NINE_ROUTER_BASE_URL environment variables
  - Registration in image_generation capability
  - Optional live test (gated by RUN_LIVE_NINEROUTER env var)
"""

import base64
import io
import os
from pathlib import Path
import pytest

from tools.graphics.nine_router_image import NineRouterImage
from tools.tool_registry import registry


@pytest.fixture
def nine_router_tool():
    """Initialize NineRouterImage."""
    return NineRouterImage()


class TestNineRouterImageRegistration:
    """Test tool registration and status."""

    def test_tool_instantiates(self, nine_router_tool):
        """Verify NineRouterImage can be instantiated."""
        assert nine_router_tool is not None
        assert isinstance(nine_router_tool, NineRouterImage)

    def test_tool_has_correct_name(self, nine_router_tool):
        """Verify tool name is 'nine_router_image'."""
        assert nine_router_tool.name == "nine_router_image"

    def test_tool_has_correct_capability(self, nine_router_tool):
        """Verify tool is registered for image_generation capability."""
        assert nine_router_tool.capability == "image_generation"

    def test_tool_has_correct_provider(self, nine_router_tool):
        """Verify tool provider is 'nine_router'."""
        assert nine_router_tool.provider == "nine_router"

    def test_tool_status_reflects_env_vars(self, nine_router_tool):
        """Verify tool status: AVAILABLE iff both API_KEY and BASE_URL are set."""
        has_key = "NINE_ROUTER_API_KEY" in os.environ
        has_url = "NINE_ROUTER_BASE_URL" in os.environ

        status = nine_router_tool.get_status()

        if has_key and has_url:
            # If both set, should be AVAILABLE or higher
            assert status.name in ("AVAILABLE", "CONFIGURED", "READY"), \
                f"Expected AVAILABLE status with both env vars, got {status.name}"
        else:
            # If either missing, should report unavailable
            assert status.name in ("UNAVAILABLE", "MISSING_DEPENDENCY"), \
                f"Expected UNAVAILABLE status without both env vars, got {status.name}"

    def test_tool_declares_env_dependencies(self, nine_router_tool):
        """Verify tool indicates NINE_ROUTER_API_KEY and NINE_ROUTER_BASE_URL as dependencies."""
        support_envelope = nine_router_tool.get_info()
        install_instructions = support_envelope.get("install_instructions", "")

        assert "NINE_ROUTER_API_KEY" in install_instructions, \
            f"NINE_ROUTER_API_KEY not mentioned in install_instructions: {install_instructions}"
        assert "NINE_ROUTER_BASE_URL" in install_instructions, \
            f"NINE_ROUTER_BASE_URL not mentioned in install_instructions: {install_instructions}"

    def test_tool_declares_text_to_image_capability(self, nine_router_tool):
        """Verify tool lists text-to-image in its capabilities."""
        assert "text_to_image" in nine_router_tool.capabilities, \
            f"text_to_image not in capabilities: {nine_router_tool.capabilities}"

    def test_tool_has_valid_tier(self, nine_router_tool):
        """Verify tool tier is set."""
        assert nine_router_tool.tier is not None
        assert str(nine_router_tool.tier).startswith("ToolTier"), \
            f"Invalid tier: {nine_router_tool.tier}"


class TestNineRouterImageToolRegistry:
    """Test tool registration in global registry."""

    def test_registry_discovers_nine_router(self):
        """Verify registry can discover NineRouterImage."""
        registry.discover()

        # Get all image_generation tools
        image_gen_tools = registry.get_by_capability("image_generation")
        tool_names = [t.name for t in image_gen_tools]

        assert "nine_router_image" in tool_names, \
            f"nine_router_image not in registry image_generation tools: {tool_names}"

    def test_registry_nine_router_has_provider(self):
        """Verify registry entry includes provider info."""
        registry.discover()

        image_gen_tools = registry.get_by_capability("image_generation")
        nine_router = next((t for t in image_gen_tools if t.name == "nine_router_image"), None)

        assert nine_router is not None, "nine_router_image not found in registry"
        assert nine_router.provider == "nine_router"


class TestNineRouterImageInputSchema:
    """Validate tool input contract."""

    def test_input_schema_requires_prompt(self, nine_router_tool):
        """Verify input schema requires 'prompt' field."""
        schema = nine_router_tool.input_schema
        assert "required" in schema
        assert "prompt" in schema["required"], \
            f"'prompt' not in required fields: {schema['required']}"

    def test_input_schema_has_size_property(self, nine_router_tool):
        """Verify input schema includes 'size' property."""
        schema = nine_router_tool.input_schema
        assert "properties" in schema
        assert "size" in schema["properties"], \
            f"'size' not in properties: {schema['properties'].keys()}"

    def test_input_schema_is_valid_json_schema(self, nine_router_tool):
        """Verify input schema is a valid JSON schema."""
        schema = nine_router_tool.input_schema
        assert "type" in schema, "Schema missing 'type' field"
        assert schema["type"] == "object", f"Expected type='object', got {schema['type']}"


class TestNineRouterImageLiveCall:
    """Optional live test: gated by RUN_LIVE_NINEROUTER environment variable."""

    @pytest.mark.skipif(
        not (os.getenv("RUN_LIVE_NINEROUTER") and os.getenv("NINE_ROUTER_API_KEY") and os.getenv("NINE_ROUTER_BASE_URL")),
        reason="RUN_LIVE_NINEROUTER or credentials not set; live test skipped"
    )
    def test_tool_live_call_produces_valid_png(self, nine_router_tool):
        """Live test: call NineRouter with a simple prompt, verify PNG response."""
        # LIVE call — will consume gateway quota
        result = nine_router_tool.execute({
            "prompt": "a single red circle on white background",
            "size": "512x512",
            "output_dir": str(Path(__file__).resolve().parent / "live_test_output"),
        })

        assert result.success, f"Live call failed: {result.error}"
        assert "image_path" in result.data, \
            f"Missing image_path in result: {result.data.keys()}"

        image_path = Path(result.data["image_path"])
        assert image_path.exists(), f"Image file not created: {image_path}"

        # Verify it's a valid PNG
        with open(image_path, "rb") as f:
            header = f.read(8)
            # PNG header: 89 50 4E 47 0D 0A 1A 0A
            assert header == b'\x89PNG\r\n\x1a\n', \
                f"File is not a valid PNG (invalid header): {header.hex()}"

    @pytest.mark.skipif(
        not (os.getenv("RUN_LIVE_NINEROUTER") and os.getenv("NINE_ROUTER_API_KEY") and os.getenv("NINE_ROUTER_BASE_URL")),
        reason="RUN_LIVE_NINEROUTER or credentials not set; live test skipped"
    )
    def test_tool_live_call_returns_base64_on_success(self, nine_router_tool):
        """Live test: verify response includes base64-encoded image data."""
        result = nine_router_tool.execute({
            "prompt": "a simple blue square",
            "size": "256x256",
            "output_dir": str(Path(__file__).resolve().parent / "live_test_output"),
        })

        assert result.success, f"Live call failed: {result.error}"

        # Check for base64 data in result
        if "image_base64" in result.data:
            b64_data = result.data["image_base64"]
            # Try to decode to verify it's valid base64
            try:
                decoded = base64.b64decode(b64_data)
                # Verify it starts with PNG header
                assert decoded[:8] == b'\x89PNG\r\n\x1a\n', \
                    "Decoded base64 is not a valid PNG"
            except Exception as e:
                pytest.fail(f"Base64 decode failed: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
