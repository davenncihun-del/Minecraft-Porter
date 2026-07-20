package net.server.transformer;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MetadataTransformer {
    private final String targetVersion;
    private final String resolvedLoaderVersion;

    public MetadataTransformer(String targetVersion) {
        this.targetVersion = targetVersion;
        this.resolvedLoaderVersion = computeLoaderVersion(targetVersion);
    }

    public String rewriteMetadata(String filename, String content) {
        if (filename.endsWith("mcmod.info")) {
            return content.replaceAll("\"mcversion\"\\s*:\\s*\"[^\"]*\"", "\"mcversion\": \"" + targetVersion + "\"");
        }
        
        if (filename.endsWith("fabric.mod.json")) {
            String updated = content.replaceAll("\"minecraft\"\\s*:\\s*\"[^\"]*\"", "\"minecraft\": \">=" + targetVersion + "\"");
            return updated.replaceAll("\"fabricloader\"\\s*:\\s*\"[^\"]*\"", "\"fabricloader\": \">=0.19.3\"");
        }

        if (filename.endsWith(".toml")) {
            return processToml(content);
        }

        if (filename.endsWith(".refmap.json")) {
            // Also update any raw reference maps that hardcode synthetic targets
            return content.replace("lambda$new$8", "lambda$new$10");
        }

        return content;
    }

    private String processToml(String content) {
        String[] lines = content.split("\\r?\\n");
        StringBuilder sb = new StringBuilder();
        String currentSection = "";
        String currentModLoader = "";
        String currentModId = ""; // We NEED this back!

        Pattern sectionPattern = Pattern.compile("^\\s*\\[{1,2}([^\\]]+)\\]{1,2}");
        Pattern modIdPattern = Pattern.compile("^\\s*modId\\s*=\\s*[\"']([^\"']+)[\"']");
        Pattern modLoaderPattern = Pattern.compile("^\\s*modLoader\\s*=\\s*[\"']([^\"']+)[\"']");

        for (String line : lines) {
            String cleanLine = line.split("#")[0].trim();
            
            // 1. Track the current section
            Matcher sectionMatcher = sectionPattern.matcher(cleanLine);
            if (sectionMatcher.find()) {
                currentSection = sectionMatcher.group(1).trim();
                currentModLoader = ""; 
                currentModId = ""; // Reset modId when we enter a new section
            }

            // 2. Track the loader (for the main mods table)
            Matcher modLoaderMatcher = modLoaderPattern.matcher(cleanLine);
            if (modLoaderMatcher.find()) {
                currentModLoader = modLoaderMatcher.group(1).trim().toLowerCase();
            }

            // 3. Track the specific dependency Mod ID inside the table
            Matcher modIdMatcher = modIdPattern.matcher(cleanLine);
            if (modIdMatcher.find()) {
                currentModId = modIdMatcher.group(1).trim().toLowerCase();
            }

            // 4. Update versionRange ONLY for the core dependencies, using currentModId!
            if (currentSection.startsWith("dependencies.") && cleanLine.startsWith("versionRange")) {
                
                // Now we check what the ACTUAL dependency is, ignoring the section header
                if (currentModId.equals("minecraft")) {
                    line = line.replaceAll("versionRange\\s*=\\s*[\"']([^\"']*)[\"']", "versionRange = \"[" + targetVersion + ",)\"");
                } 
                else if (currentModId.equals("neoforge") || currentModId.equals("forge")) {
                    line = line.replaceAll("versionRange\\s*=\\s*[\"']([^\"']*)[\"']", "versionRange = \"" + resolvedLoaderVersion + "\"");
                } 
                else if (currentModId.equals("javafml")) {
                    line = line.replaceAll("versionRange\\s*=\\s*[\"']([^\"']*)[\"']", "versionRange = \"[0,)\""); 
                }
                // If it is 'flywheel' or 'ponder', it skips these checks and preserves the original version!
            }

            // Processing main loader specs inside the mod definition
            if ((currentSection.equals("mods") || currentSection.startsWith("mods.")) && cleanLine.startsWith("loaderVersion")) {
                if (!currentModLoader.equals("javafml")) {
                    line = line.replaceAll("loaderVersion\\s*=\\s*[\"']([^\"']*)[\"']", "loaderVersion = \"" + resolvedLoaderVersion + "\"");
                } else {
                    line = line.replaceAll("loaderVersion\\s*=\\s*[\"']([^\"']*)[\"']", "loaderVersion = \"[0,)\"");
                }
            }

            sb.append(line).append("\n");
        }
        return sb.toString();
    }

    private String computeLoaderVersion(String targetVersion) {
        String core = targetVersion.startsWith("1.") ? targetVersion.substring(2) : targetVersion;
        String[] parts = core.split("\\.");
        
        try {
            int major = Integer.parseInt(parts[0]);
            int minor = parts.length > 1 ? Integer.parseInt(parts[1]) : 0;
            
            if (major >= 20) {
                return "[" + major + "." + minor + ",)";
            }
        } catch (NumberFormatException ignored) {}
        
        return "[21.1,)"; // Predictable 1.21 baseline default fallback
    }
}