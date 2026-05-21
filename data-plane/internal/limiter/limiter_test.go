package limiter

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestRedisLimiterAllowUsesConfiguredWindowAndKey(t *testing.T) {
	client := &fakeRedisClient{result: 1}
	limiter, err := NewRedisLimiter(RedisConfig{
		Limit:       3,
		Window:      2 * time.Second,
		KeyPrefix:   "test-limiter",
		RedisClient: client,
	})
	if err != nil {
		t.Fatalf("new limiter: %v", err)
	}

	if err := limiter.Allow(context.Background(), "sink:s3"); err != nil {
		t.Fatalf("allow: %v", err)
	}

	if client.key != "test-limiter:sink:s3" {
		t.Fatalf("unexpected key: %s", client.key)
	}
	if client.args[0] != 3 || client.args[1] != int64(2000) {
		t.Fatalf("unexpected args: %#v", client.args)
	}
}

func TestRedisLimiterReturnsRateLimitError(t *testing.T) {
	client := &fakeRedisClient{result: 0}
	limiter, err := NewRedisLimiter(RedisConfig{
		Limit:       1,
		Window:      time.Second,
		RedisClient: client,
	})
	if err != nil {
		t.Fatalf("new limiter: %v", err)
	}

	err = limiter.Allow(context.Background(), "global")
	if !errors.Is(err, ErrLimited) {
		t.Fatalf("expected ErrLimited, got %v", err)
	}
}

func TestRedisLimiterReturnsRedisError(t *testing.T) {
	client := &fakeRedisClient{err: errors.New("redis down")}
	limiter, err := NewRedisLimiter(RedisConfig{
		Limit:       1,
		Window:      time.Second,
		RedisClient: client,
	})
	if err != nil {
		t.Fatalf("new limiter: %v", err)
	}

	err = limiter.Allow(context.Background(), "global")
	if err == nil || !errors.Is(err, client.err) {
		t.Fatalf("expected redis error, got %v", err)
	}
}

func TestRedisLimiterRejectsInvalidConfig(t *testing.T) {
	if _, err := NewRedisLimiter(RedisConfig{Limit: 0, Window: time.Second}); err == nil {
		t.Fatal("expected limit validation error")
	}
	if _, err := NewRedisLimiter(RedisConfig{Limit: 1, Window: 0}); err == nil {
		t.Fatal("expected window validation error")
	}
}

type fakeRedisClient struct {
	result int
	err    error
	key    string
	args   []interface{}
	closed bool
}

func (c *fakeRedisClient) Eval(
	_ context.Context,
	_ string,
	keys []string,
	args ...interface{},
) *redis.Cmd {
	c.key = keys[0]
	c.args = args
	if c.err != nil {
		return redis.NewCmdResult(nil, c.err)
	}
	return redis.NewCmdResult(int64(c.result), nil)
}

func (c *fakeRedisClient) Close() error {
	c.closed = true
	return nil
}
