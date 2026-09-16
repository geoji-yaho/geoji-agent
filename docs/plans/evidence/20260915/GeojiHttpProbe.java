import java.net.URI;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.io.ByteArrayInputStream;
public class GeojiHttpProbe {
  public static void main(String[] args) throws Exception {
    String body = """
      {"schema_version":1,"submission_id":"local-http-probe","payload_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","mode":"INITIAL","post_type":"spent","amount_krw":6100,"category":"카페/간식","item":"스타벅스","reason":null}
      """;
    for (boolean http1 : new boolean[]{false, true}) {
      for (boolean streaming : new boolean[]{false, true}) {
        var builder = HttpClient.newBuilder();
        if (http1) builder.version(HttpClient.Version.HTTP_1_1);
        try (var client = builder.build()) {
          var publisher = streaming
            ? HttpRequest.BodyPublishers.ofInputStream(() -> new ByteArrayInputStream(body.getBytes(StandardCharsets.UTF_8)))
            : HttpRequest.BodyPublishers.ofString(body);
          var request = HttpRequest.newBuilder(URI.create("http://127.0.0.1:18100/internal/v1/intake"))
            .header("Authorization", "Bearer geoji-local-service")
            .header("Content-Type", "application/json").POST(publisher).build();
          var response = client.send(request, HttpResponse.BodyHandlers.ofString());
          System.out.println("http1="+http1+" streaming="+streaming+" status="+response.statusCode()+" response="+response.body());
        }
      }
    }
  }
}
