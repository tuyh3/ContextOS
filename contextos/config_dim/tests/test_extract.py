from contextos.config_dim.extract import extract_config_refs, normalize_key

JAVA = '''
package com.x.offer;
import org.springframework.beans.factory.annotation.Value;

@org.springframework.context.annotation.Configuration("offer-switch")
public class OfferConfig {
    @Value("${jdbc.url}")
    private String url;
    @Value("${offer.threshold:2000}")
    private int threshold;

    public void load() {
        String v = props.getProperty("OFFER_PERM_SWITCH");
    }
}
'''


def test_normalize_key():
    assert normalize_key("${jdbc.url}") == "jdbc.url"
    assert normalize_key("${offer.threshold:2000}") == "offer.threshold"
    assert normalize_key("RAW_KEY") == "RAW_KEY"


def test_extract_value_and_method_and_framework():
    refs = extract_config_refs("com/x/offer/OfferConfig.java", JAVA, framework_annotations=["Configuration"])
    by_key = {r.key_norm: r for r in refs}
    # @Value
    assert "jdbc.url" in by_key and by_key["jdbc.url"].ref_type == "annotation_value"
    assert "offer.threshold" in by_key
    # getProperty 方法参数
    assert "OFFER_PERM_SWITCH" in by_key and by_key["OFFER_PERM_SWITCH"].ref_type == "method_arg"
    # 自研框架注解 @Configuration("offer-switch")
    assert "offer-switch" in by_key and by_key["offer-switch"].ref_type == "annotation"
    # MEDIUM 2: FQN 以 source_path 为主锚, AST package + enclosing class
    assert by_key["jdbc.url"].class_fqn == "com.x.offer.OfferConfig"
    assert by_key["jdbc.url"].source_path == "com/x/offer/OfferConfig.java"
