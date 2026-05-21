package limiter

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

var ErrLimited = errors.New("rate limited")

type Limiter interface {
	Allow(ctx context.Context, key string) error
	Close() error
}

type NoopLimiter struct{}

func (NoopLimiter) Allow(context.Context, string) error { return nil }
func (NoopLimiter) Close() error                        { return nil }

type RedisConfig struct {
	URL         string
	Limit       int
	Window      time.Duration
	KeyPrefix   string
	Timeout     time.Duration
	RedisClient RedisClient
}

type RedisClient interface {
	Eval(ctx context.Context, script string, keys []string, args ...interface{}) *redis.Cmd
	Close() error
}

type RedisLimiter struct {
	client  RedisClient
	limit   int
	window  time.Duration
	prefix  string
	timeout time.Duration
}

const fixedWindowScript = `
local current = redis.call("INCR", KEYS[1])
if current == 1 then
  redis.call("PEXPIRE", KEYS[1], ARGV[2])
end
if current > tonumber(ARGV[1]) then
  return 0
end
return 1
`

func NewRedisLimiter(cfg RedisConfig) (*RedisLimiter, error) {
	if cfg.Limit <= 0 {
		return nil, fmt.Errorf("redis limiter limit must be positive")
	}
	if cfg.Window <= 0 {
		return nil, fmt.Errorf("redis limiter window must be positive")
	}
	prefix := cfg.KeyPrefix
	if prefix == "" {
		prefix = "data-plane:limiter"
	}
	timeout := cfg.Timeout
	if timeout <= 0 {
		timeout = time.Second
	}

	client := cfg.RedisClient
	if client == nil {
		options, err := redis.ParseURL(cfg.URL)
		if err != nil {
			return nil, fmt.Errorf("parse redis url: %w", err)
		}
		options.DialTimeout = timeout
		options.ReadTimeout = timeout
		options.WriteTimeout = timeout
		client = redis.NewClient(options)
	}

	return &RedisLimiter{
		client:  client,
		limit:   cfg.Limit,
		window:  cfg.Window,
		prefix:  prefix,
		timeout: timeout,
	}, nil
}

func (l *RedisLimiter) Allow(ctx context.Context, key string) error {
	if key == "" {
		key = "global"
	}
	allowCtx, cancel := context.WithTimeout(ctx, l.timeout)
	defer cancel()

	windowMilliseconds := l.window.Milliseconds()
	if windowMilliseconds <= 0 {
		windowMilliseconds = 1
	}
	result, err := l.client.Eval(
		allowCtx,
		fixedWindowScript,
		[]string{l.prefix + ":" + key},
		l.limit,
		windowMilliseconds,
	).Int()
	if err != nil {
		return fmt.Errorf("redis limiter acquire: %w", err)
	}
	if result != 1 {
		return fmt.Errorf("%w: key=%s limit=%d window=%s", ErrLimited, key, l.limit, l.window)
	}
	return nil
}

func (l *RedisLimiter) Close() error {
	return l.client.Close()
}
