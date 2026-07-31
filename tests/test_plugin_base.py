import pytest

from payload.core.errors import PluginApiVersionError
from payload.core.ir import PLUGIN_API_VERSION
from payload.core.plugin_base import check_api_compatibility


def test_check_api_compatibility_accepts_matching_major():
    check_api_compatibility("mio_plugin", PLUGIN_API_VERSION)  # non deve sollevare


def test_check_api_compatibility_accepts_different_minor():
    major = PLUGIN_API_VERSION.split(".")[0]
    check_api_compatibility("mio_plugin", f"{major}.99")  # non deve sollevare


def test_check_api_compatibility_rejects_different_major():
    with pytest.raises(PluginApiVersionError):
        check_api_compatibility("mio_plugin", "999.0")
