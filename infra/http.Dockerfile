FROM golang:1.27.1-bookworm AS build
WORKDIR /src
COPY infra/http/ ./
RUN CGO_ENABLED=0 go build -trimpath -o /fixture .
FROM scratch
COPY --from=build /fixture /fixture
USER 65534:65534
ENTRYPOINT ["/fixture"]
