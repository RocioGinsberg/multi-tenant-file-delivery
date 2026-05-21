package source

import (
	"context"
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
	AccessKeyID     string
	SecretAccessKey string
	UsePathStyle    bool
}

type S3ObjectFetcher struct {
	client *s3.Client
}

func NewS3ObjectFetcher(ctx context.Context, cfg S3Config) (*S3ObjectFetcher, error) {
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
		return nil, fmt.Errorf("load s3 source config: %w", err)
	}
	client := s3.NewFromConfig(awsCfg, func(o *s3.Options) {
		o.UsePathStyle = cfg.UsePathStyle
		if cfg.Endpoint != "" {
			o.BaseEndpoint = aws.String(cfg.Endpoint)
		}
	})
	return &S3ObjectFetcher{client: client}, nil
}

func (f *S3ObjectFetcher) GetObject(ctx context.Context, bucket, key string) ([]byte, error) {
	output, err := f.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, fmt.Errorf("get source object %q/%q: %w", bucket, key, err)
	}
	defer output.Body.Close()
	data, err := io.ReadAll(output.Body)
	if err != nil {
		return nil, fmt.Errorf("read source object %q/%q: %w", bucket, key, err)
	}
	return data, nil
}

func (f *S3ObjectFetcher) CheckBucket(ctx context.Context, bucket string) error {
	if bucket == "" {
		return fmt.Errorf("staging bucket is required")
	}
	_, err := f.client.HeadBucket(ctx, &s3.HeadBucketInput{
		Bucket: aws.String(bucket),
	})
	if err != nil {
		return fmt.Errorf("check source bucket %q: %w", bucket, err)
	}
	return nil
}
