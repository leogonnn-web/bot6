package exchange

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"
)

const restBase = "https://api.bybit.com"
const restTest = "https://api-testnet.bybit.com"

// RESTClient is a thin synchronous wrapper around Bybit V5 REST.
type RESTClient struct {
	apiKey    string
	apiSecret string
	host      string
	client    *http.Client
	limiter   *TokenBucket
}

// NewRESTClient creates a signed REST client.
func NewRESTClient(apiKey, apiSecret string, testnet bool) *RESTClient {
	host := restBase
	if testnet {
		host = restTest
	}
	return &RESTClient{
		apiKey:    apiKey,
		apiSecret: apiSecret,
		host:      host,
		client:    &http.Client{Timeout: 5 * time.Second},
	}
}

func (c *RESTClient) sign(ts, recvWindow, payload string) string {
	raw := ts + c.apiKey + recvWindow + payload
	h := hmac.New(sha256.New, []byte(c.apiSecret))
	h.Write([]byte(raw))
	return hex.EncodeToString(h.Sum(nil))
}

func (c *RESTClient) request(method, path, query, body string) ([]byte, error) {
	if c.limiter != nil {
		c.limiter.Wait()
	}
	ts := strconv.FormatInt(time.Now().UnixMilli(), 10)
	recvWindow := "5000"
	url := c.host + path
	if query != "" {
		url += "?" + query
	}
	req, err := http.NewRequest(method, url, bytes.NewBufferString(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-BAPI-API-KEY", c.apiKey)
	req.Header.Set("X-BAPI-TIMESTAMP", ts)
	req.Header.Set("X-BAPI-RECV-WINDOW", recvWindow)
	req.Header.Set("X-BAPI-SIGN", c.sign(ts, recvWindow, body))
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}

// OrderReq is a generic create-order request body.
type OrderReq struct {
	Category         string `json:"category"`
	Symbol           string `json:"symbol"`
	Side             string `json:"side"`
	OrderType        string `json:"orderType"`
	Qty              string `json:"qty"`
	MarketUnit       string `json:"marketUnit,omitempty"`
	Price            string `json:"price,omitempty"`
	TriggerPrice     string `json:"triggerPrice,omitempty"`
	TriggerDirection int    `json:"triggerDirection,omitempty"` // 1=rise, 2=fall
	OrderFilter      string `json:"orderFilter,omitempty"`      // StopOrder
	OrderLinkId      string `json:"orderLinkId,omitempty"`
}

// OrderResp generic response wrapper.
type OrderResp struct {
	RetCode int    `json:"retCode"`
	RetMsg  string `json:"retMsg"`
	Result  struct {
		OrderId     string `json:"orderId"`
		OrderLinkId string `json:"orderLinkId"`
	} `json:"result"`
}

// OrderDetail returned by realtime query.
type OrderDetail struct {
	OrderId      string  `json:"orderId"`
	Status       string  `json:"orderStatus"`
	CumExecQty   float64 `json:"cumExecQty,string"`
	CumExecValue float64 `json:"cumExecValue,string"`
	AvgPrice     float64 `json:"avgPrice,string"`
	Qty          float64 `json:"qty,string"`
	Side         string  `json:"side"`
}

// CreateOrder places an order and returns the orderId.
func (c *RESTClient) CreateOrder(r OrderReq) (string, error) {
	b, _ := json.Marshal(r)
	raw, err := c.request("POST", "/v5/order/create", "", string(b))
	if err != nil {
		return "", err
	}
	var resp OrderResp
	if err := json.Unmarshal(raw, &resp); err != nil {
		return "", err
	}
	if resp.RetCode != 0 {
		return "", fmt.Errorf("bybit createOrder error %d: %s", resp.RetCode, resp.RetMsg)
	}
	return resp.Result.OrderId, nil
}

// CancelOrder cancels by orderId.
func (c *RESTClient) CancelOrder(category, symbol, orderId string) error {
	body := fmt.Sprintf(`{"category":"%s","symbol":"%s","orderId":"%s"}`, category, symbol, orderId)
	_, err := c.request("POST", "/v5/order/cancel", "", body)
	return err
}

// GetOrder queries a single order status.
func (c *RESTClient) GetOrder(category, symbol, orderId string) (*OrderDetail, error) {
	query := fmt.Sprintf("category=%s&symbol=%s&orderId=%s", category, symbol, orderId)
	raw, err := c.request("GET", "/v5/order/realtime", query, "")
	if err != nil {
		return nil, err
	}
	var wrap struct {
		RetCode int `json:"retCode"`
		Result  struct {
			List []OrderDetail `json:"list"`
		} `json:"result"`
	}
	if err := json.Unmarshal(raw, &wrap); err != nil {
		return nil, err
	}
	if wrap.RetCode != 0 || len(wrap.Result.List) == 0 {
		return nil, fmt.Errorf("order not found or error %d", wrap.RetCode)
	}
	return &wrap.Result.List[0], nil
}

// GetWalletBalance returns USDT available equity.
func (c *RESTClient) GetWalletBalance() (float64, error) {
	query := "accountType=UNIFIED&coin=USDT"
	raw, err := c.request("GET", "/v5/account/wallet-balance", query, "")
	if err != nil {
		return 0, err
	}
	var wrap struct {
		RetCode int `json:"retCode"`
		Result  struct {
			List []struct {
				Coin []struct {
					Coin                string `json:"coin"`
					WalletBalance       string `json:"walletBalance"`
					AvailableToWithdraw string `json:"availableToWithdraw"`
				} `json:"coin"`
			} `json:"list"`
		} `json:"result"`
	}
	if err := json.Unmarshal(raw, &wrap); err != nil {
		return 0, err
	}
	if wrap.RetCode != 0 {
		return 0, fmt.Errorf("wallet error %d", wrap.RetCode)
	}
	for _, acct := range wrap.Result.List {
		for _, coin := range acct.Coin {
			if coin.Coin == "USDT" {
				v, _ := strconv.ParseFloat(coin.AvailableToWithdraw, 64)
				return v, nil
			}
		}
	}
	return 0, nil
}
