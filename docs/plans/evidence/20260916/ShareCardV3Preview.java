import com.ttegeoji.backend.media.CardRenderer;
import com.ttegeoji.backend.media.MediaProperties;
import com.ttegeoji.backend.media.MediaStorage;
import com.ttegeoji.backend.verdictview.dto.MemeView;
import com.ttegeoji.backend.verdictview.dto.ShareCardResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

class ShareCardV3Preview {
    public static void main(String[] args) throws Exception {
        var renderer = new CardRenderer(new MediaProperties("/private/tmp/geoji-card-preview",
                "/System/Library/Fonts/AppleSDGothicNeo.ttc", "", ""));
        String paragraph = "배심원들은 이번 소비의 필요성과 대체 가능한 선택지를 함께 살폈습니다. 예산과 계획을 비교했을 때 추가 지출을 잠시 미루는 편이 좋겠습니다. 오늘의 결정은 다음 소비를 더 신중하게 만드는 기회입니다. ";
        String statement = paragraph.repeat(2) + "충동적인 선택을 멈추고 필요한 물건인지 다시 확인하는 시간을 가져보세요. 차분히 생각하세요. 마지막 문장도 잘림 없이 끝까지 표시합니다.";
        if (statement.codePointCount(0, statement.length()) != 300) throw new IllegalStateException("300자 경계 픽스처 오류");
        String sentence = "형량: 내일 하루 무지출을 실천하고 다음 소비 전에 필요성과 대안을 다시 확인하세요.";
        var card = new ShareCardResponse("local-layout-fixture", "spent", "guilty", "mild",
                "긴 문장도 마지막 글자까지 담은 판결", List.of(statement), "oneDay", sentence,
                new MemeView("GUILTY_HEAVY", "layout-fixture", "local-fixture"));
        byte[] image = Files.readAllBytes(Path.of(args[1]));
        byte[] png = renderer.render(card, image);
        Path output = Path.of(args[0]);
        Files.createDirectories(output.getParent());
        Files.write(output, png);
        System.out.println("template=" + CardRenderer.TEMPLATE_VERSION);
        System.out.println("statement_codepoints=" + statement.codePointCount(0, statement.length()));
        System.out.println("png_sha256=" + MediaStorage.hash(png));
        System.out.println("meme_sha256=" + MediaStorage.hash(image));
        System.out.println("output=" + output);
    }
}
