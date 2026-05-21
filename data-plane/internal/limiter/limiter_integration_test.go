package limiter

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"
)

func TestRedisLimiterDocker(t *testing.T) {
	if os.Getenv("RUN_DOCKER_TESTS") != "1" {
		t.Skip("set RUN_DOCKER_TESTS=1 with deploy/docker-compose.yml redis running")
	}

	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		redisURL = "redis://localhost:6379/0"
	}
	limiter, err := NewRedisLimiter(RedisConfig{
		URL:    redisURL,
		Limit:  1,
		Window: time.Second,
	})
	if err != nil {
		t.Fatalf("new limiter: %v", err)
	}
	defer limiter.Close()

	key := "docker:" + time.Now().UTC().Format("20060102150405.000000000")
	if err := limiter.Allow(context.Background(), key); err != nil {
		t.Fatalf("first allow: %v", err)
	}
	if err := limiter.Allow(context.Background(), key); !errors.Is(err, ErrLimited) {
		t.Fatalf("expected rate limit, got %v", err)
	}

	time.Sleep(1100 * time.Millisecond)
	if err := limiter.Allow(context.Background(), key); err != nil {
		t.Fatalf("allow after window: %v", err)
	}
}
