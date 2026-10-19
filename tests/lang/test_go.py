from textwrap import dedent

from crgraph.lang import parse_source, plugin_for
from crgraph.model import Ref

SRC = '''
package payments

import (
	"fmt"
	str "strings"
	"github.com/acme/shop/internal/cart"
)

type Client struct {
	cart.Base
	http *Http
}

type Payer interface {
	Pay(amount int) error
}

const Max, min = 10, 1

func (c *Client) Pay(amount, extra int, cur string) (bool, error) {
	if amount > 0 && cur != "" {
		return cart.Charge(amount), nil
	}
	x := &Order{ID: 1}
	return fmt.Sprintf("%d", x) != str.ToUpper(""), nil
}

func newClient() *Client { return &Client{} }
'''


def parse(path: str, src: str):
    return parse_source(plugin_for(path), path, dedent(src).lstrip().encode())


def test_symbols_visibility_and_metrics():
    facts = parse("internal/payments/client.go", SRC)
    by = {s.qualname: s for s in facts.symbols}
    assert set(by) == {"Client", "Payer", "Payer.Pay", "Max", "min", "Client.Pay", "newClient"}
    assert (by["Client"].kind, by["Payer"].kind) == ("class", "interface")
    assert by["Client"].supertypes == ("Base",)  # embedded struct
    assert by["Client"].signature == "Client struct"
    assert by["Client.Pay"].parent == "Client" and by["Client.Pay"].arity == 3
    assert by["Client.Pay"].complexity == 3
    assert by["Client.Pay"].signature == "func (c *Client) Pay(amount, extra int, cur string) (bool, error)"
    assert by["newClient"].visibility == "internal" and by["min"].visibility == "internal"
    assert by["Max"].visibility == "public" and by["Max"].signature == "Max, min"  # never the values
    assert facts.package == "internal/payments"  # a Go package is its directory


def test_imports_and_refs():
    facts = parse("internal/payments/client.go", SRC)
    assert facts.imports == ["fmt", "strings", "github.com/acme/shop/internal/cart"]
    assert Ref("call", "Charge", "Client.Pay") in facts.refs
    assert Ref("type", "Order", "Client.Pay") in facts.refs
    assert Ref("inherit", "Base", "Client") in facts.refs
