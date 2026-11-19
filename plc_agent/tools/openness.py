"""可选：接入 TIA Portal Openness 做真正的编译校验。

限制（很重要，别在 Linux 上白折腾）：
- 只能在 Windows 上运行，且必须安装 TIA Portal（V16 / V17 / V18）；
- 需要 Python.NET（pythonnet）加载 Siemens.Engineering.dll；
- 需要 Windows 用户加入 "Siemens TIA Openness" 用户组，否则 Openness 拒绝连接；
- 首次连接要在 TIA 里弹信任对话框，纯无人值守场景需提前放行。

推荐落地方式（三步）：
1) 在本 Agent 里生成 .scl；
2) 在 Windows 侧 CI Runner 上用 TIA 导入 SCL 到测试项目并编译：
       ImportOptions 里设置 Overwrite = true；
       CompileProvider.Compile() 拿到 CompilerResult；
3) 把编译错误（ErrorCount / Messages）回传给本 Agent 的 repair 节点，
   与静态校验报告合并成 issues_text —— 这一步比任何 prompt 调优都有效。
"""
from typing import Any, Dict


class OpennessUnavailable(RuntimeError):
    pass


def compile_scl_with_openness(project_path: str, scl_path: str,
                              plc_name: str = None) -> Dict[str, Any]:
    try:
        import clr  # pythonnet
    except ImportError as exc:  # pragma: no cover
        raise OpennessUnavailable(
            "未检测到 pythonnet / TIA Portal Openness。该能力仅支持 Windows + TIA Portal V16+。"
        ) from exc

    # 以下代码需在 Windows 的 TIA 环境中运行，这里保留为可运行的参考实现：
    #
    #   clr.AddReference(r"C:\Program Files\Siemens\Automation\Portal V18\PublicAPI\V18\"
    #                    r"Siemens.Engineering.dll")
    #   from Siemens.Engineering import TiaPortal, TiaPortalMode
    #   from Siemens.Engineering.HW import HardwareObject
    #   tia = TiaPortal(TiaPortalMode.WithUserInterface)
    #   project = tia.Projects.Open(project_path)
    #   # 定位 PLC 软件 → 导入外部源文件 → 编译
    #   ...
    raise OpennessUnavailable(
        "Openness 编译需要真实 TIA Portal 工程与许可证，本仓库只提供接口与接线方式（见模块 docstring）。"
    )
