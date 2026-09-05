"""适配器注册表：集中构建所有协议适配器与共享元数据。

新增协议：在此 import 并加入 build_adapters() 即可，调用方无感知。
"""
import os

from parser_lib.core.metadata import MetadataStore
from parser_lib.adapters.adapter_645 import DLT645Adapter
from parser_lib.adapters.adapter_698 import DLT69845Adapter
from parser_lib.adapters.adapter_10376 import QGDW103762Adapter
from parser_lib.adapters.adapter_dualmode import DualMode43Adapter
from parser_lib.adapters.adapter_dualmac import DualMacAdapter

_HERE = os.path.dirname(__file__)


def build_adapters() -> "tuple[list, MetadataStore]":
    store = MetadataStore()
    store.load_protocol("645", os.path.join(_HERE, "adapter_645", "metadata"))
    store.load_protocol("698.45", os.path.join(_HERE, "adapter_698", "metadata"))
    store.load_protocol("1376.2", os.path.join(_HERE, "adapter_10376", "metadata"))
    adapters = [
        DualMode43Adapter(),     # 双模4-3以 0x11/0x12/0x1A 起始，必须早于内层 645/698
        DLT645Adapter(metadata_store=store),
        DLT69845Adapter(metadata_store=store),
        QGDW103762Adapter(),   # 注册在最后，作为双 0x68 帧的兜底路由
    ]
    return adapters, store


def build_gw_adapters() -> "tuple[list, MetadataStore]":
    """GW 侦听台封装帧（7E FF 02 空口镜像）专用适配器组。

    与 build_adapters 分离：GW 帧的嗅探以 DualMacAdapter 为入口
    （4-2 链路层/NWK 层），内层 1376.2 透传由调用方按需二次解析。
    """
    store = MetadataStore()
    store.load_protocol("645", os.path.join(_HERE, "adapter_645", "metadata"))
    store.load_protocol("698.45", os.path.join(_HERE, "adapter_698", "metadata"))
    store.load_protocol("1376.2", os.path.join(_HERE, "adapter_10376", "metadata"))
    return [DualMacAdapter()], store
