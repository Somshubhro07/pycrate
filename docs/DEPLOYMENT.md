# PyCrate Deployment Guide

Step-by-step instructions for deploying PyCrate to AWS EC2 + Vercel.

---

## Prerequisites

- AWS account (free tier eligible)
- GitHub account
- Vercel account (hobby plan, free)
- MongoDB Atlas account (M0 free cluster)

---

## 1. MongoDB Atlas Setup

1. Go to [MongoDB Atlas](https://www.mongodb.com/atlas)
2. Create a free M0 cluster (any region)
3. Create a database user with read/write permissions
4. Add `0.0.0.0/0` to the IP Access List (or your EC2 IP specifically)
5. Get the connection string: click "Connect" > "Connect your application"
6. It will look like: `mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/pycrate?retryWrites=true&w=majority`

---

## 2. EC2 Instance Setup

### Launch Instance

1. Go to AWS EC2 console
2. Launch instance:
   - **AMI**: Ubuntu 22.04 LTS (HVM, SSD)
   - **Type**: t2.micro (free tier)
   - **Key pair**: Create new or use existing (.pem file)
   - **Security group**: Create new with these rules:
     - SSH (22) from your IP
     - Custom TCP (8000) from anywhere (or Vercel IPs only)
3. Launch and wait for it to start

### Connect and Bootstrap

```bash
# SSH into the instance
ssh -i your-key.pem ubuntu@YOUR_EC2_PUBLIC_IP

# Download and run the setup script
git clone https://github.com/Somshubhro07/pycrate.git /opt/pycrate
cd /opt/pycrate
sudo chmod +x infrastructure/setup-ec2.sh
sudo ./infrastructure/setup-ec2.sh
```

### Configure Environment

```bash
sudo nano /opt/pycrate/.env
```

Set these values:
```
PYCRATE_API_KEY=<generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))">
PYCRATE_MONGODB_URI=mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/pycrate?retryWrites=true&w=majority
PYCRATE_CORS_ORIGINS=http://localhost:3000,https://your-app.vercel.app
```

### Start the Service

```bash
sudo systemctl start pycrate
sudo systemctl status pycrate

# Verify it works
curl http://localhost:8000/api/health
```

---

## 3. Vercel Dashboard Setup

### Deploy from GitHub

1. Push the repository to GitHub
2. Go to [Vercel](https://vercel.com) and import the repository
3. Set the root directory to `dashboard`
4. Set environment variables:
   - `NEXT_PUBLIC_API_URL` = `http://YOUR_EC2_PUBLIC_IP:8000`
   - `NEXT_PUBLIC_API_KEY` = same key you set in `.env` on EC2
5. Deploy

### Custom Domain (Optional)

1. In Vercel project settings > Domains
2. Add your custom domain (e.g., `pycrate.yourdomain.com`)
3. Update DNS as instructed by Vercel

---

## 4. GitHub Actions CI/CD

### Required Secrets

Set these in GitHub > Repository > Settings > Secrets and variables > Actions:

| Secret | Value |
|---|---|
| `EC2_HOST` | Your EC2 public IP |
| `EC2_SSH_KEY` | Contents of your .pem private key |
| `API_URL` | `http://YOUR_EC2_IP:8000` |
| `API_KEY` | Your PYCRATE_API_KEY value |
| `VERCEL_TOKEN` | From Vercel account settings > Tokens |
| `VERCEL_ORG_ID` | From `.vercel/project.json` after first deploy |
| `VERCEL_PROJECT_ID` | From `.vercel/project.json` after first deploy |

After setting secrets, every push to `main` will auto-deploy both the
dashboard and the engine.

---

## 5. Security Hardening

### Restrict EC2 Security Group

Instead of allowing port 8000 from anywhere, restrict to Vercel's IP ranges:

```bash
# Vercel's egress IPs (check Vercel docs for current list)
aws ec2 authorize-security-group-ingress \
    --group-id sg-XXXXX \
    --protocol tcp \
    --port 8000 \
    --cidr 76.76.21.0/24
```

### Use HTTPS (Optional)

1. Install nginx on EC2: `sudo apt install nginx`
2. Copy the nginx config: `sudo cp infrastructure/nginx.conf /etc/nginx/sites-available/pycrate`
3. Get a free SSL certificate via Let's Encrypt:
   ```bash
   sudo apt install certbot python3-certbot-nginx
   sudo certbot --nginx -d your-domain.com
   ```
4. Update `NEXT_PUBLIC_API_URL` in Vercel to use `https://`

---

## Troubleshooting

**Service won't start:**
```bash
sudo journalctl -u pycrate -n 50 --no-pager
```

**cgroups v2 not available:**
```bash
mount | grep cgroup2
# If empty, your kernel doesn't support cgroups v2.
# Ubuntu 22.04+ should have it by default.
```

**MongoDB connection refused:**
- Check Atlas IP Access List includes your EC2 IP
- Verify the connection string in `.env`

**Dashboard can't reach API:**
- Check EC2 security group allows port 8000
- Verify `NEXT_PUBLIC_API_URL` in Vercel environment variables
- Check CORS origins in `.env` on EC2
