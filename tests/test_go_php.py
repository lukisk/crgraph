"""End to end on small Go and PHP repositories: modules, imports, cycles, API changes and tests."""
from helpers import findings, run_range

from crgraph.render.jsonout import to_document

GO_MOD = "module github.com/acme/shop\n\ngo 1.22\n"
CART = "package cart\n\nfunc Total(items []int) int {\n\treturn len(items)\n}\n"
PAY = ('package payments\n\nimport "github.com/acme/shop/internal/cart"\n\n'
       "func Charge(items []int) int {\n\treturn cart.Total(items)\n}\n")


def test_go_packages_imports_cycle_and_api_change(repo):
    base = repo.commit({"go.mod": GO_MOD, "internal/cart/cart.go": CART, "internal/payments/pay.go": PAY})
    head = repo.commit({
        "internal/cart/cart.go": ('package cart\n\nimport "github.com/acme/shop/internal/payments"\n\n'
                                  "func Total(items []int, tax int) int {\n\treturn payments.Charge(items) + tax\n}\n"),
        "internal/cart/cart_test.go": "package cart\n\nfunc TestTotal() {}\n",
    })
    result = run_range(repo, base, head)
    cycle = findings(result, "new-cycle")
    assert any("internal/cart" in f.title and "internal/payments" in f.title for f in cycle)
    added = [f.title for f in findings(result, "module-dep-added")]
    assert added == ["New dependency github.com/acme/shop/internal/cart → github.com/acme/shop/internal/payments (via imports)"]
    assert any("Total" in f.title for f in findings(result, "api-change"))
    doc = to_document(result)
    nodes = {n["id"]: n for n in doc["nodes"]}
    assert nodes["module:github.com/acme/shop/internal/cart"]["status"] == "modified"
    assert nodes["file:internal/cart/cart_test.go"]["test"] is True
    assert not any("internal/cart" in f.title for f in findings(result, "test-gap"))


COMPOSER = '{"name": "acme/shop", "autoload": {"psr-4": {"App\\\\": "src/"}}, "autoload-dev": {"psr-4": {"Tests\\\\": "tests/"}}}'
REPO = ("<?php\nnamespace App\\Repository;\n\nclass ProductRepository\n{\n"
        "    public function find(string $id): string { return $id; }\n}\n")
CHECKOUT = ("<?php\nnamespace App\\Service;\n\nuse App\\Repository\\ProductRepository;\n\nclass Checkout\n{\n"
            "    public function __construct(private ProductRepository $repo) {}\n"
            "    public function buy(string $id): string { return $this->repo->find($id); }\n}\n")


def test_php_namespaces_imports_cycle_api_change_and_tests_elsewhere(repo):
    base = repo.commit({"composer.json": COMPOSER, "src/Repository/ProductRepository.php": REPO,
                        "src/Service/Checkout.php": CHECKOUT})
    head = repo.commit({
        "src/Repository/ProductRepository.php": REPO.replace("namespace App\\Repository;\n", "namespace App\\Repository;\n\nuse App\\Service\\Checkout;\n")
                                                    .replace("find(string $id)", "find(string $id, string $currency)"),
        "src/Service/Checkout.php": CHECKOUT.replace("find($id)", "find($id, 'EUR')"),
        "tests/Unit/CheckoutTest.php": "<?php\nnamespace Tests\\Unit;\n\nclass CheckoutTest {}\n",
    })
    result = run_range(repo, base, head)
    assert any("App\\Repository" in f.title and "App\\Service" in f.title for f in findings(result, "new-cycle"))
    assert any("ProductRepository.find" in f.title for f in findings(result, "api-change"))
    doc = to_document(result)
    nodes = {n["id"]: n for n in doc["nodes"]}
    assert "file:src/Service/Checkout.php->file:src/Repository/ProductRepository.php:imports" in {
        f"{e['src']}->{e['dst']}:{e['kind']}" for e in doc["edges"]}
    assert nodes["module:Tests\\Unit"]["test"] is True
    # the test for Checkout lives in tests/, not next to it: still no test gap for App\Service
    assert not any("App\\Service" in f.title for f in findings(result, "test-gap"))
