from textwrap import dedent

from crgraph.config import Config
from crgraph.modules import detect_modules


def detect(files: dict[str, str], config: Config | None = None):
    blobs = {k: dedent(v).encode() for k, v in files.items()}
    return detect_modules(sorted(blobs), lambda paths: {p: blobs.get(p) for p in paths}, config or Config())


def test_gradle_includes_project_dirs_and_dependencies():
    mm = detect({
        "settings.gradle.kts": '''
            include(":app")
            include(":platform:core-time", ":feature:detail")
            // include(":disabled")
            project(":feature:detail").projectDir = File(rootProject.projectDir, "features/detail")
            pluginManagement { repositories { maven { url = uri("http://example.com/x") } } }
        ''',
        "app/build.gradle.kts": '''
            dependencies {
                implementation(project(":feature:detail"))
                implementation(projects.platform.coreTime)
                implementation(libs.androidx.core.ktx)
                implementation("com.squareup.okhttp3:okhttp:4.12.0")
            }
            plugins { alias(libs.plugins.android.application) }
        ''',
        "features/detail/build.gradle.kts": "dependencies { api(projects.platform.coreTime.dependencyProject) }",
        "platform/core-time/build.gradle.kts": "",
        "app/src/main/kotlin/A.kt": "",
    })
    assert {m: i.root for m, i in mm.modules.items()} == {
        ":app": "app", ":platform:core-time": "platform/core-time", ":feature:detail": "features/detail",
    }
    assert mm.deps == {(":app", ":feature:detail"), (":app", ":platform:core-time"),
                       (":feature:detail", ":platform:core-time")}
    assert mm.external[":app"] == {"libs.androidx.core.ktx", "com.squareup.okhttp3:okhttp"}
    assert mm.module_of("app/src/main/kotlin/A.kt") == ":app"


def test_gradle_settings_in_subdirectory_prefixes_ids():
    mm = detect({
        "app/settings.gradle.kts": 'include(":app")\ninclude(":architecture")\n',
        "app/app/build.gradle.kts": 'dependencies { implementation(project(":architecture")) }',
        "app/architecture/build.gradle.kts": "",
    })
    assert mm.modules["app:app"].root == "app/app"
    assert mm.deps == {("app:app", "app:architecture")}


def test_swiftpm_targets_and_sources_convention():
    mm = detect({
        "pkg/Package.swift": '''
            let package = Package(
                name: "Kit",
                targets: [
                    .target(name: "KitCore", dependencies: [.product(name: "Danger", package: "swift")]),
                    .target(name: "KitUI", dependencies: ["KitCore", .target(name: "KitCore")], path: "UI"),
                    .testTarget(name: "KitCoreTests", dependencies: ["KitCore"]),
                ]
            )
        ''',
        "pkg/Sources/KitCore/A.swift": "",
        "ios/Frameworks/ShopCore/Sources/Config.swift": "",
        "ios/Frameworks/ShopCore/Tests/ConfigTests.swift": "",
    })
    assert mm.modules["KitCore"].root == "pkg/Sources/KitCore"
    assert mm.modules["KitUI"].root == "pkg/UI"
    assert mm.modules["KitCoreTests"].root == "pkg/Tests/KitCoreTests"
    assert ("KitUI", "KitCore") in mm.deps and ("KitCoreTests", "KitCore") in mm.deps
    assert mm.external["KitCore"] == {"swiftpm:swift/Danger"}
    assert mm.modules["ShopCore"].root == "ios/Frameworks/ShopCore"
    assert mm.module_of("ios/Frameworks/ShopCore/Tests/ConfigTests.swift") == "ShopCore"


def test_npm_workspaces():
    mm = detect({
        "package.json": '{"name": "root", "workspaces": ["packages/*"]}',
        "packages/ui/package.json": '{"name": "@acme/ui", "dependencies": {"@acme/core": "1", "react": "18"}}',
        "packages/core/package.json": '{"name": "@acme/core"}',
        "console/package.json": "{not json",
    })
    assert set(mm.modules) == {"@acme/ui", "@acme/core"}
    assert mm.deps == {("@acme/ui", "@acme/core")}
    assert mm.external["@acme/ui"] == {"npm:react"}


def test_python_pyproject_and_requirements():
    mm = detect({
        "clients/python/pyproject.toml": '[project]\nname = "quiz-client"\ndependencies = ["httpx>=0.27", "pydantic[email]"]\n',
        "clients/python/requirements.txt": "# pinned\nrich==13\n-e .\n",
    })
    assert mm.modules["quiz-client"].root == "clients/python"
    assert mm.external["quiz-client"] == {"pip:httpx", "pip:pydantic", "pip:rich"}


def test_fallback_and_overrides():
    cfg = Config(modules={"app/src/**/domain/**": "domain"})
    mm = detect({"settings.gradle.kts": 'include(":app")'}, cfg)
    assert mm.module_of("app/src/main/x/domain/A.kt") == "domain"
    assert mm.modules["domain"].root == "app/src"
    assert mm.module_of("app/src/main/x/ui/B.kt") == ":app"
    assert mm.module_of("tools/scripts/deep/run.py") == "tools/scripts"
    assert mm.module_of("README.md") == "(root)"


def test_ignored_build_files_are_skipped():
    mm = detect({"build/generated/package.json": '{"name": "gen"}'})
    assert mm.modules == {}


def test_go_packages_are_modules_named_by_import_path():
    mm = detect({
        "go.mod": """
            module github.com/acme/shop

            go 1.22

            require (
            \tgithub.com/stretchr/testify v1.9.0
            \tgolang.org/x/sync v0.7.0 // indirect
            )
            require github.com/google/uuid v1.6.0
        """,
        "main.go": "package main",
        "internal/cart/cart.go": "package cart",
        "internal/cart/cart_test.go": "package cart",
        "internal/cart/testdata/fixture.go": "package fixture",
    })
    ids = {mid: (info.root, info.system) for mid, info in mm.modules.items()}
    assert ids == {"github.com/acme/shop": ("", "go"), "github.com/acme/shop/internal/cart": ("internal/cart", "go")}
    assert mm.module_of("internal/cart/cart_test.go") == "github.com/acme/shop/internal/cart"
    assert mm.external["github.com/acme/shop"] == {"go:github.com/stretchr/testify", "go:github.com/google/uuid"}


def test_composer_packages_and_psr4_namespaces():
    mm = detect({
        "composer.json": """
            {"name": "acme/shop",
             "require": {"php": ">=8.2", "ext-json": "*", "symfony/http-kernel": "^7.0", "acme/money": "*"},
             "autoload": {"psr-4": {"App\\\\": "src/"}},
             "autoload-dev": {"psr-4": {"App\\\\Tests\\\\": "tests/"}}}
        """,
        "packages/money/composer.json": '{"name": "acme/money", "autoload": {"psr-4": {"Acme\\\\Money\\\\": "src"}}}',
        "src/Kernel.php": "<?php",
        "src/Service/Payment/Stripe.php": "<?php",
        "tests/Unit/CheckoutTest.php": "<?php",
        "packages/money/src/Money.php": "<?php",
    })
    ids = {mid: info.root for mid, info in mm.modules.items()}
    assert ids == {"acme/shop": "", "acme/money": "packages/money", "App": "src", "App\\Service\\Payment": "src/Service/Payment",
                   "App\\Tests\\Unit": "tests/Unit", "Acme\\Money": "packages/money/src"}
    assert mm.deps == {("acme/shop", "acme/money")}
    assert mm.external["acme/shop"] == {"composer:symfony/http-kernel"}
