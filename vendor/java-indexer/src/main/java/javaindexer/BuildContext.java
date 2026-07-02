package javaindexer;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.Collections;
import java.util.List;

/**
 * Input specification: which modules to index, their source roots and classpath.
 * Deserialized from build_context.json produced by build_context.py.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class BuildContext {

    @JsonProperty("java_version")
    public String javaVersion = "1.8";

    public List<Module> modules = Collections.emptyList();

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Module {
        public String name = "";

        @JsonProperty("source_roots")
        public List<String> sourceRoots = Collections.emptyList();

        @JsonProperty("classpath_entries")
        public List<String> classpathEntries = Collections.emptyList();

        public String encoding = "UTF-8";
    }
}
