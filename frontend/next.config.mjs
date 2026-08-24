/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export: the frontend is HTML/JS/CSS on S3 behind CloudFront, with no
  // server compute at all (ADR-0012). Everything dynamic comes from the API.
  // Cost at demo scale is roughly $0.50/month rather than an SSR runtime.
  output: 'export',
  reactStrictMode: true,
  images: { unoptimized: true },  // no server = no image optimizer
  trailingSlash: true,            // maps cleanly onto S3 object keys
};

export default nextConfig;
