FROM node:20-alpine

RUN apk add --no-cache python3 py3-pip jq && \
    pip install mcp-proxy --break-system-packages

RUN npm install -g @tomaspavlin/rohlik-mcp

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
