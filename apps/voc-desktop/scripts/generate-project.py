#!/usr/bin/env python3
"""Generate the small, dependency-free Xcode project deterministically."""

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
        debug = {"SWIFT_OPTIMIZATION_LEVEL": "-Onone", "ENABLE_TESTABILITY": "YES"}
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


# Source lists stay explicit so entry points never leak into the unhosted tests.
app_files = sorted(ROOT.glob("Sources/**/*.swift"))
core_files = [
    path
    for path in app_files
    if path.name in ("ConnectionProfile.swift", "ConnectionModel.swift")
]
test_files = sorted(ROOT.glob("Tests/*.swift"))
references = {
    path: add(
        "PBXFileReference",
        lastKnownFileType="sourcecode.swift",
        path=str(path.relative_to(ROOT)),
        sourceTree="<group>",
    )
    for path in app_files + test_files
}
products = []
targets = []
for name, extension, kind, files in (
    ("VOCDataState", "app", "application", app_files),
    ("VOCDataStateTests", "xctest", "bundle.unit-test", core_files + test_files),
):
    product = add(
        "PBXFileReference",
        includeInIndex=0,
        path=f"{name}.{extension}",
        explicitFileType="wrapper.application"
        if extension == "app"
        else "wrapper.cfbundle",
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
        "GENERATE_INFOPLIST_FILE": "NO" if extension == "app" else "YES",
    }
    if extension == "app":
        settings.update(
            INFOPLIST_FILE="Resources/Info.plist",
            CODE_SIGN_ENTITLEMENTS="Resources/VOCDataState.entitlements",
            ENABLE_APP_SANDBOX="YES",
        )
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
        )
    )

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
