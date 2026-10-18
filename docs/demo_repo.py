"""Builds a small multi-module Kotlin repository with one reviewable change, for screenshots and demos.

    python docs/demo_repo.py <new-dir>
    cd <new-dir> && crgraph main...feature/quick-buy
"""
import os
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
root.mkdir(parents=True)


def git(*args):
    env = os.environ | {"GIT_AUTHOR_NAME": "demo", "GIT_AUTHOR_EMAIL": "demo@example.com",
                        "GIT_COMMITTER_NAME": "demo", "GIT_COMMITTER_EMAIL": "demo@example.com"}
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)


def write(files):
    for path, text in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def gradle(mods):
    files = {"settings.gradle.kts": "".join(f'include("{m}")\n' for m in mods)}
    for m, deps in mods.items():
        files[m.strip(":").replace(":", "/") + "/build.gradle.kts"] = (
            "dependencies {\n" + "".join(f'    implementation(project("{d}"))\n' for d in deps) + "}\n")
    return files


def kt(module, pkg, name, body):
    return {f"{module}/src/main/kotlin/com/example/shop/{pkg.replace('.', '/')}/{name}.kt": f"package com.example.shop.{pkg}\n\n{body}"}


base_mods = {
    ":app": [":feature:catalog", ":feature:checkout", ":core:ui"],
    ":feature:catalog": [":core:data", ":core:ui"],
    ":feature:checkout": [":core:data", ":core:ui"],
    ":core:data": [":core:network"],
    ":core:network": [],
    ":core:ui": [],
}
files = gradle(base_mods)
files |= kt("core/network", "network", "HttpClient", "class HttpClient {\n    fun request(path: String): String = path\n}\n")
files |= kt("core/data", "data", "ProductRepository", """import com.example.shop.network.HttpClient

class ProductRepository(private val http: HttpClient) {
    fun products(): List<String> = listOf(http.request("products"))
    fun product(id: String): String = http.request("products/$id")
}
""")
files |= kt("core/data", "data", "CartRepository", """import com.example.shop.network.HttpClient

class CartRepository(private val http: HttpClient) {
    fun items(): List<String> = listOf(http.request("cart"))
    fun add(id: String) { http.request("cart/add/$id") }
}
""")
files |= kt("core/ui", "ui", "PriceText", "fun formatPrice(cents: Int): String = \"${cents / 100}.${cents % 100}\"\n")
files |= kt("feature/catalog", "catalog", "CatalogViewModel", """import com.example.shop.data.ProductRepository
import com.example.shop.ui.formatPrice

class CatalogViewModel(private val repo: ProductRepository) {
    fun load(): List<String> = repo.products()
    fun price(cents: Int) = formatPrice(cents)
}
""")
files |= kt("feature/catalog", "catalog", "ProductScreen", """import com.example.shop.data.ProductRepository

class ProductScreen(private val repo: ProductRepository) {
    fun show(id: String) = repo.product(id)
}
""")
files |= kt("feature/checkout", "checkout", "CheckoutViewModel", """import com.example.shop.data.CartRepository

class CheckoutViewModel(private val cart: CartRepository) {
    fun items() = cart.items()
}
""")
files |= kt("app", "app", "MainActivity", """import com.example.shop.catalog.CatalogViewModel
import com.example.shop.checkout.CheckoutViewModel

class MainActivity(private val catalog: CatalogViewModel, private val checkout: CheckoutViewModel) {
    fun start() { catalog.load(); checkout.items() }
}
""")
files[".crgraph.yml"] = "rules:\n  - forbid: { from: \"feature/**\", to: \"feature/**\" }\n"
files["core/data/src/test/kotlin/com/example/shop/data/CartRepositoryTest.kt"] = "package com.example.shop.data\n\nclass CartRepositoryTest\n"
write(files)
git("init", "-q", "-b", "main")
git("add", "-A")
git("commit", "-q", "-m", "shop app")
git("checkout", "-q", "-b", "feature/quick-buy")

# the change: checkout reaches into catalog, a repository API is re-signed, a new payment module appears
head_mods = dict(base_mods)
head_mods[":feature:checkout"] = [":core:data", ":core:ui", ":feature:catalog", ":core:payments"]
head_mods[":core:payments"] = [":core:network"]
files = gradle(head_mods)
files |= kt("core/data", "data", "ProductRepository", """import com.example.shop.network.HttpClient

class ProductRepository(private val http: HttpClient) {
    fun products(): List<String> = listOf(http.request("products"))
    fun product(id: String, currency: String): String = http.request("products/$id?currency=$currency")
}
""")
files |= kt("feature/catalog", "catalog", "ProductScreen", """import com.example.shop.data.ProductRepository

class ProductScreen(private val repo: ProductRepository) {
    fun show(id: String) = repo.product(id, "EUR")
}
""")
files |= kt("core/payments", "payments", "PaymentClient", """import com.example.shop.network.HttpClient

class PaymentClient(private val http: HttpClient) {
    fun pay(cents: Int): Boolean = http.request("pay/$cents").isNotEmpty()
}
""")
files |= kt("feature/checkout", "checkout", "QuickBuy", """import com.example.shop.catalog.ProductScreen
import com.example.shop.payments.PaymentClient

class QuickBuy(private val screen: ProductScreen, private val payments: PaymentClient) {
    fun buy(id: String, cents: Int): Boolean {
        screen.show(id)
        if (cents <= 0) return false
        if (cents > 100_000) return false
        return payments.pay(cents)
    }
}
""")
files |= kt("feature/checkout", "checkout", "CheckoutViewModel", """import com.example.shop.data.CartRepository

class CheckoutViewModel(private val cart: CartRepository, private val quickBuy: QuickBuy) {
    fun items() = cart.items()
    fun buyNow(id: String) = quickBuy.buy(id, 1999)
}
""")
write(files)
git("add", "-A")
git("commit", "-q", "-m", "Quick buy from the product screen")
