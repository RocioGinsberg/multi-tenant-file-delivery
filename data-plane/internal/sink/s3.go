package sink

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

type S3Config struct {
	Endpoint        string
	Region          string
	Bucket          string
	AccessKeyID     string
	SecretAccessKey string
	UsePathStyle    bool
}

type S3Sink struct {
	bucket string
	client *s3.Client
}

func NewS3Sink(ctx context.Context, cfg S3Config) (*S3Sink, error) {
	if cfg.Bucket == "" {
		return nil, fmt.Errorf("s3 bucket is required")
	}
	if cfg.Region == "" {
		cfg.Region = "us-east-1"
	}

	loadOptions := []func(*config.LoadOptions) error{
		config.WithRegion(cfg.Region),
		config.WithRequestChecksumCalculation(aws.RequestChecksumCalculationWhenRequired),
	}
	if cfg.AccessKeyID != "" || cfg.SecretAccessKey != "" {
		loadOptions = append(loadOptions, config.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(cfg.AccessKeyID, cfg.SecretAccessKey, ""),
		))
	}

	awsCfg, err := config.LoadDefaultConfig(ctx, loadOptions...)
	if err != nil {
		return nil, fmt.Errorf("load s3 config: %w", err)
	}

	client := s3.NewFromConfig(awsCfg, func(o *s3.Options) {
		o.UsePathStyle = cfg.UsePathStyle
		if cfg.Endpoint != "" {
			o.BaseEndpoint = aws.String(cfg.Endpoint)
		}
	})
	return &S3Sink{bucket: cfg.Bucket, client: client}, nil
}

func (s *S3Sink) Name() string { return "s3" }

func (s *S3Sink) Check(ctx context.Context) error {
	_, err := s.client.HeadBucket(ctx, &s3.HeadBucketInput{
		Bucket: aws.String(s.bucket),
	})
	if err != nil {
		return fmt.Errorf("check s3 sink bucket %q: %w", s.bucket, err)
	}
	return nil
}

func (s *S3Sink) Upload(ctx context.Context, src Source, meta Meta) (Receipt, error) {
	reader, err := src.Open()
	if err != nil {
		return Receipt{}, err
	}

	hash := sha256.New()
	if _, err := io.Copy(hash, reader); err != nil {
		reader.Close()
		return Receipt{}, fmt.Errorf("hash source %q: %w", src.Path(), err)
	}
	if err := reader.Close(); err != nil {
		return Receipt{}, fmt.Errorf("close source %q: %w", src.Path(), err)
	}

	size, err := src.Size()
	if err != nil {
		return Receipt{}, err
	}

	body, err := src.Open()
	if err != nil {
		return Receipt{}, err
	}
	defer body.Close()

	_, err = s.client.PutObject(ctx, &s3.PutObjectInput{
		Bucket:        aws.String(s.bucket),
		Key:           aws.String(meta.DstPath),
		Body:          body,
		ContentLength: aws.Int64(size),
	})
	if err != nil {
		return Receipt{}, fmt.Errorf("put s3 object %q: %w", meta.DstPath, err)
	}
	return Receipt{Key: meta.DstPath, Size: size, SHA256: hex.EncodeToString(hash.Sum(nil))}, nil
}

func (s *S3Sink) Close() error { return nil }
