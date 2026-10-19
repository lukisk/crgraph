from textwrap import dedent

from crgraph.lang import parse_source, plugin_for
from crgraph.model import Ref

SRC = r'''
<?php
namespace App\Service;

use App\Repository\ProductRepository;
use App\Model\{Order, Cart as Basket};
use function App\Util\money;

final class Checkout extends Base implements Payer, \Countable
{
    use LoggerTrait;
    private const MAX = 3;
    protected ?ProductRepository $repo = null;

    public function buy(string $id, ?int $qty = null): Order
    {
        if ($qty > 0 && $id !== '' || $x ?? false) {
            return new Order($this->repo->find($id), Basket::empty());
        }
        foreach ([1] as $i) { money($i); }
        return \App\Util\Helpers::make();
    }

    private static function hidden(): void {}
}

interface Payer extends Base2 { public function pay(int $a): bool; }
enum Status: string { case Ok = 'ok'; }
function helper($a, $b) { return strlen($a); }
'''


def parse(path: str, src: str):
    return parse_source(plugin_for(path), path, dedent(src).lstrip().encode())


def test_symbols_visibility_and_metrics():
    facts = parse("src/Service/Checkout.php", SRC)
    by = {s.qualname: s for s in facts.symbols}
    assert set(by) == {"Checkout", "Checkout.MAX", "Checkout.repo", "Checkout.buy", "Checkout.hidden",
                       "Payer", "Payer.pay", "Status", "helper"}
    assert by["Checkout"].supertypes == ("Base", "Payer", "Countable", "LoggerTrait")
    assert (by["Payer"].kind, by["Status"].kind) == ("interface", "enum")
    assert by["Checkout.buy"].signature == "public function buy(string $id, ?int $qty = null): Order"
    assert by["Checkout.buy"].arity == 2 and by["Checkout.buy"].complexity == 6
    assert by["Checkout.hidden"].visibility == "private" and by["Checkout.repo"].visibility == "internal"
    assert by["Checkout.MAX"].signature == "MAX"  # never the value
    assert by["Payer.pay"].signature == "public function pay(int $a): bool"
    assert facts.package == "App\\Service"


def test_use_statements_and_refs():
    facts = parse("src/Service/Checkout.php", SRC)
    assert facts.imports == ["App\\Repository\\ProductRepository", "App\\Model\\Order", "App\\Model\\Cart",
                             "App\\Util\\money"]
    names = {r.name for r in facts.refs}
    assert {"Order", "Helpers", "ProductRepository"} <= names
    assert not {"App", "Util", "money"} & {r.name for r in facts.refs if r.kind == "type"}  # namespaces, functions
    assert Ref("call", "find", "Checkout.buy") in facts.refs
    assert Ref("inherit", "LoggerTrait", "Checkout") in facts.refs
