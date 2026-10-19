#!/usr/bin/env python3
"""Generate the native targets and their pinned MCP SDK dependency deterministically."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "VOCDataState.xcodeproj"
objects = {}


def add(kind, **values):
    identity = f"{len(objects) + 1:024X}"
    objects[identity] = {"isa": kind, **values}
    return identity


def encode(value, depth=0):
    if isinstance(value, dict):
        entries = [
            "  " * (depth + 1) + f"{json.dumps(key)} = {encode(item, depth + 1)};"
            for key, item in value.items()
        ]
        return "{\n" + "\n".join(entries) + "\n" + "  " * depth + "}"
    if isinstance(value, list):
        return "(" + ", ".join(encode(item, depth) for item in value) + ")"
    return json.dumps(str(value))


def configurations(settings):
    children = []
    for name in ("Debug", "Release"):
        debug = {"SWIFT_OPTIMIZATION_LEVEL": "-Onone", "ENABLE_TESTABILITY": "YES", "ONLY_ACTIVE_ARCH": "YES"}
        children.append(
            add(
                "XCBuildConfiguration",
                name=name,
                buildSettings={**settings, **(debug if name == "Debug" else {})},
            )
        )
    return add(
        "XCConfigurationList",
        buildConfigurations=children,
        defaultConfigurationIsVisible=0,
        defaultConfigurationName="Debug",
    )


# Each executable has its own entry point; the handoff core is compiled into both.
shared_files = sorted(ROOT.glob("Sources/DesktopCore/*.swift")) + [
    ROOT / "Sources/VOCDataState/ConnectionProfile.swift"
]
app_files = sorted(ROOT.glob("Sources/VOCDataState/*.swift")) + [
    path for path in shared_files if path.name != "ConnectionProfile.swift"
]
mcp_files = sorted(ROOT.glob("Sources/DesktopMCP/*.swift"))
bridge_files = shared_files + mcp_files + sorted(ROOT.glob("Sources/DesktopBridge/*.swift"))
core_files = shared_files + mcp_files + [
    ROOT / "Sources/VOCDataState/ConnectionModel.swift",
    ROOT / "Sources/VOCDataState/HandoffController.swift",
]
test_files = sorted(ROOT.glob("Tests/*.swift"))
references = {
    path: add(
        "PBXFileReference",
        lastKnownFileType="sourcecode.swift",
        path=str(path.relative_to(ROOT)),
        sourceTree="<group>",
    )
    for path in sorted(set(app_files + bridge_files + test_files))
}
mcp_package = add(
    "XCRemoteSwiftPackageReference",
    repositoryURL="https://github.com/modelcontextprotocol/swift-sdk.git",
    requirement={"kind": "exactVersion", "version": "0.12.1"},
)
products = []
targets = []
for name, extension, kind, files in (
    ("VOCDataState", "app", "application", app_files),
    ("VOCDataStateTests", "xctest", "bundle.unit-test", core_files + test_files),
    ("VOCDesktopBridge", "", "tool", bridge_files),
):
    product = add(
        "PBXFileReference",
        includeInIndex=0,
        path=f"{name}.{extension}" if extension else name,
        explicitFileType="wrapper.application"
        if extension == "app"
        else ("wrapper.cfbundle" if extension else "compiled.mach-o.executable"),
        sourceTree="BUILT_PRODUCTS_DIR",
    )
    products.append(product)
    sources = add(
        "PBXSourcesBuildPhase",
        buildActionMask=2147483647,
        files=[add("PBXBuildFile", fileRef=references[path]) for path in files],
        runOnlyForDeploymentPostprocessing=0,
    )
    frameworks = add(
        "PBXFrameworksBuildPhase",
        buildActionMask=2147483647,
        files=[],
        runOnlyForDeploymentPostprocessing=0,
    )
    settings = {
        "PRODUCT_NAME": "$(TARGET_NAME)",
        "PRODUCT_BUNDLE_IDENTIFIER": "dev.voc.data-state"
        + (".tests" if extension == "xctest" else ""),
        "GENERATE_INFOPLIST_FILE": "YES" if extension == "xctest" else "NO",
    }
    if extension == "app":
        settings.update(
            INFOPLIST_FILE="Resources/Info.plist",
            CODE_SIGN_ENTITLEMENTS="Resources/VOCDataState.entitlements",
            ENABLE_APP_SANDBOX="NO",
        )
    package_products = []
    if name in ("VOCDesktopBridge", "VOCDataStateTests"):
        dependency = add("XCSwiftPackageProductDependency", package=mcp_package, productName="MCP")
        package_products.append(dependency)
        objects[frameworks]["files"].append(add("PBXBuildFile", productRef=dependency))
    targets.append(
        add(
            "PBXNativeTarget",
            name=name,
            productName=name,
            productReference=product,
            productType="com.apple.product-type." + kind,
            buildConfigurationList=configurations(settings),
            buildPhases=[sources, frameworks],
            buildRules=[],
            dependencies=[],
            packageProductDependencies=package_products,
        )
    )

# Bundle the helper so it always resolves its own known application, not a PATH command.
objects[targets[0]]["dependencies"].append(add("PBXTargetDependency", target=targets[2]))
objects[targets[0]]["buildPhases"].append(add(
    "PBXCopyFilesBuildPhase",
    buildActionMask=2147483647,
    dstPath="Contents/Helpers",
    dstSubfolderSpec=1,
    files=[add("PBXBuildFile", fileRef=products[2], settings={"ATTRIBUTES": ["CodeSignOnCopy"]})],
    name="Embed desktop helper",
    runOnlyForDeploymentPostprocessing=0,
))

products_group = add(
    "PBXGroup", children=products, name="Products", sourceTree="<group>"
)
main_group = add(
    "PBXGroup",
    children=list(references.values()) + [products_group],
    sourceTree="<group>",
)
project = add(
    "PBXProject",
    attributes={"LastUpgradeCheck": "2710"},
    buildConfigurationList=configurations(
        {
            "SDKROOT": "macosx",
            "MACOSX_DEPLOYMENT_TARGET": "15.0",
            "SWIFT_VERSION": "5.0",
            "CLANG_ENABLE_MODULES": "YES",
            "OTHER_LDFLAGS": ["$(inherited)", "-lsqlite3"],
            "CODE_SIGN_IDENTITY": "-",
            "CODE_SIGN_STYLE": "Manual",
            "ENABLE_HARDENED_RUNTIME": "YES",
        }
    ),
    compatibilityVersion="Xcode 14.0",
    developmentRegion="es",
    knownRegions=["es", "en", "Base"],
    mainGroup=main_group,
    productRefGroup=products_group,
    projectDirPath="",
    projectRoot="",
    targets=targets,
    packageReferences=[mcp_package],
)
PROJECT.mkdir(exist_ok=True)
(PROJECT / "project.pbxproj").write_text(
    "// !$*UTF8*$!\n"
    + encode(
        {
            "archiveVersion": 1,
            "classes": {},
            "objectVersion": 56,
            "objects": objects,
            "rootObject": project,
        }
    )
    + "\n"
)


def reference(index):
    name = ("VOCDataState", "VOCDataStateTests")[index]
    extension = ("app", "xctest")[index]
    return f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{targets[index]}" BuildableName="{name}.{extension}" BlueprintName="{name}" ReferencedContainer="container:VOCDataState.xcodeproj"/>'


scheme = f"""<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="2710" version="1.3">
  <BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries>
    <BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">{reference(0)}</BuildActionEntry>
    <BuildActionEntry buildForTesting="YES" buildForRunning="NO" buildForProfiling="NO" buildForArchiving="NO" buildForAnalyzing="YES">{reference(1)}</BuildActionEntry>
  </BuildActionEntries></BuildAction>
  <TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB"><Testables><TestableReference skipped="NO" parallelizable="NO">{reference(1)}</TestableReference></Testables></TestAction>
  <LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO"><BuildableProductRunnable runnableDebuggingMode="0">{reference(0)}</BuildableProductRunnable></LaunchAction>
  <AnalyzeAction buildConfiguration="Debug"/>
  <ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>
"""
schemes = PROJECT / "xcshareddata/xcschemes"
schemes.mkdir(parents=True, exist_ok=True)
(schemes / "VOCDataState.xcscheme").write_text(scheme)
