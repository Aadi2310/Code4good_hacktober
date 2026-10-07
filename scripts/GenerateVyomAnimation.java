import java.awt.*;
import java.awt.geom.*;
import java.awt.image.BufferedImage;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import javax.imageio.*;
import javax.imageio.metadata.*;
import javax.imageio.stream.*;
import org.w3c.dom.*;

/**
 * Builds README-friendly, looping GIF overlays on the existing VYOM+ PNGs.
 * The source diagrams are loaded unchanged; animation is drawn only over them.
 */
public final class GenerateVyomAnimation {
    private static final int FPS = 8;
    private static final int FRAMES = 88; // 11 seconds at 8 fps
    private static final double DURATION = FRAMES * 0.12; // GIF frame delay below is 12/100 s
    private static final Color TEAL = new Color(83, 205, 194);
    private static final Color BLUE = new Color(111, 171, 235);
    private static final Color AMBER = new Color(241, 184, 102);
    private static final Color REVIEW = new Color(244, 139, 115);

    public static void main(String[] args) throws Exception {
        Path root = Paths.get(args.length > 0 ? args[0] : ".").toAbsolutePath().normalize();
        Path assets = root.resolve("assets");
        Files.createDirectories(assets);
        build(root.resolve("TechnologyStack.png"), assets.resolve("vyom-techstack.gif"), true);
        build(root.resolve("SystemArchitecture.png"), assets.resolve("vyom-system-architecture.gif"), false);
        writeSvgSource(root.resolve("TechnologyStack.png"), assets.resolve("vyom-techstack.svg"));
        verifyGif(assets.resolve("vyom-techstack.gif"), 1408, 768);
        verifyGif(assets.resolve("vyom-system-architecture.gif"), 768, 1376);
        System.out.println("Generated 88-frame, 11-second looping GIFs and editable SVG overlay source.");
    }

    private static void build(Path source, Path output, boolean stack) throws Exception {
        BufferedImage base = ImageIO.read(source.toFile());
        if (base == null) throw new IOException("Cannot read PNG: " + source);
        Iterator<ImageWriter> writers = ImageIO.getImageWritersBySuffix("gif");
        if (!writers.hasNext()) throw new IllegalStateException("No GIF ImageIO writer is installed.");
        ImageWriter writer = writers.next();
        try (ImageOutputStream out = ImageIO.createImageOutputStream(output.toFile())) {
            writer.setOutput(out);
            writer.prepareWriteSequence(null);
            for (int i = 0; i < FRAMES; i++) {
                double time = (double) i / FPS;
                BufferedImage frame = new BufferedImage(base.getWidth(), base.getHeight(), BufferedImage.TYPE_INT_RGB);
                Graphics2D g = frame.createGraphics();
                g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
                g.setRenderingHint(RenderingHints.KEY_STROKE_CONTROL, RenderingHints.VALUE_STROKE_PURE);
                g.drawImage(base, 0, 0, null);
                if (stack) drawStack(g, time);
                else drawArchitecture(g, time);
                g.dispose();
                writeFrame(writer, frame, i == 0);
                frame.flush();
            }
            writer.endWriteSequence();
        } finally {
            writer.dispose();
            base.flush();
        }
    }

    private static void writeFrame(ImageWriter writer, BufferedImage frame, boolean first) throws Exception {
        ImageTypeSpecifier type = ImageTypeSpecifier.createFromRenderedImage(frame);
        ImageWriteParam param = writer.getDefaultWriteParam();
        IIOMetadata metadata = writer.getDefaultImageMetadata(type, param);
        String format = metadata.getNativeMetadataFormatName();
        IIOMetadataNode root = (IIOMetadataNode) metadata.getAsTree(format);
        IIOMetadataNode gce = child(root, "GraphicControlExtension");
        gce.setAttribute("disposalMethod", "none");
        gce.setAttribute("userInputFlag", "FALSE");
        gce.setAttribute("transparentColorFlag", "FALSE");
        gce.setAttribute("delayTime", "12"); // hundredths of a second
        gce.setAttribute("transparentColorIndex", "0");
        if (first) {
            IIOMetadataNode extensions = child(root, "ApplicationExtensions");
            IIOMetadataNode loop = new IIOMetadataNode("ApplicationExtension");
            loop.setAttribute("applicationID", "NETSCAPE");
            loop.setAttribute("authenticationCode", "2.0");
            loop.setUserObject(new byte[] { 1, 0, 0 }); // infinite loop
            extensions.appendChild(loop);
        }
        metadata.setFromTree(format, root);
        writer.writeToSequence(new IIOImage(frame, null, metadata), param);
    }

    private static void verifyGif(Path file, int width, int height) throws Exception {
        Iterator<ImageReader> readers = ImageIO.getImageReadersByFormatName("gif");
        if (!readers.hasNext()) throw new IllegalStateException("No GIF reader is installed.");
        ImageReader reader = readers.next();
        try (ImageInputStream in = ImageIO.createImageInputStream(file.toFile())) {
            reader.setInput(in, false, false);
            int frames = reader.getNumImages(true);
            if (frames != FRAMES) throw new IOException(file + " contains " + frames + " frames; expected " + FRAMES);
            IIOMetadata firstMetadata = reader.getImageMetadata(0);
            Node metadataRoot = firstMetadata.getAsTree(firstMetadata.getNativeMetadataFormatName());
            if (!hasNetscapeLoop(metadataRoot)) throw new IOException(file + " is missing its infinite-loop extension");
            for (int i : new int[] {0, FRAMES / 2, FRAMES - 1}) {
                BufferedImage rendered = reader.read(i);
                if (rendered.getWidth() != width || rendered.getHeight() != height)
                    throw new IOException(file + " frame " + i + " has unexpected dimensions");
                rendered.flush();
            }
        } finally {
            reader.dispose();
        }
    }

    private static boolean hasNetscapeLoop(Node node) {
        if ("ApplicationExtension".equals(node.getNodeName())) {
            NamedNodeMap attrs = node.getAttributes();
            Node app = attrs == null ? null : attrs.getNamedItem("applicationID");
            Node auth = attrs == null ? null : attrs.getNamedItem("authenticationCode");
            if (app != null && auth != null && "NETSCAPE".equals(app.getNodeValue()) && "2.0".equals(auth.getNodeValue()))
                return true;
        }
        for (Node child = node.getFirstChild(); child != null; child = child.getNextSibling())
            if (hasNetscapeLoop(child)) return true;
        return false;
    }

    private static IIOMetadataNode child(IIOMetadataNode parent, String name) {
        for (int i = 0; i < parent.getLength(); i++)
            if (name.equals(parent.item(i).getNodeName())) return (IIOMetadataNode) parent.item(i);
        IIOMetadataNode node = new IIOMetadataNode(name);
        parent.appendChild(node);
        return node;
    }

    private static void drawStack(Graphics2D g, double t) {
        // The PNG is a radial system map. Pulses follow its existing spokes via the VYOM+ hub.
        // One smooth breath per loop; the frame-zero and final states nearly match.
        double wave = (1 - Math.cos(2 * Math.PI * t / DURATION)) / 2;
        g.setColor(withAlpha(TEAL, (float) (0.12 + 0.12 * wave)));
        g.setStroke(new BasicStroke((float) (1.8 + 0.8 * wave)));
        double r = 145 + 3.0 * wave;
        g.draw(new Ellipse2D.Double(704 - r, 384 - r, 2 * r, 2 * r));

        // Brief in-place file-badge emphasis; the raster badges themselves remain fixed.
        double input = active(t, 1.0, 1.0);
        if (input >= 0) {
            int badge = Math.min(5, (int) (input * 6));
            highlight(g, new Rectangle[] {
                new Rectangle(169, 129, 46, 57), new Rectangle(269, 129, 47, 57),
                new Rectangle(326, 129, 49, 57), new Rectangle(378, 129, 47, 57),
                new Rectangle(432, 129, 49, 57), new Rectangle(484, 129, 47, 57)
            }[badge], BLUE, 0.42);
            pulse(g, input, 1.6, new double[][] {{525,190},{625,225},{704,240}}, BLUE);
        }

        // Input -> UI -> API -> processing, using the center and existing connector spokes.
        pulseSegment(g, t, 2.0, 0.28, new double[][] {{704,239},{704,185}}, BLUE);
        pulseSegment(g, t, 2.30, 0.28, new double[][] {{704,185},{704,239}}, BLUE);
        pulseSegment(g, t, 2.60, 0.34, new double[][] {{840,278},{886,240}}, BLUE);

        // The stack artwork shows format groups and document stages, not CSV/openpyxl boxes.
        // Route the structured-file pulse through its existing input-to-core and core-to-schema arrows.
        pulseSegment(g, t, 3.0, 0.22, new double[][] {{294,181},{510,204},{625,263}}, BLUE);
        pulseSegment(g, t, 3.24, 0.22, new double[][] {{704,525},{704,560}}, BLUE);
        pulseSegment(g, t, 3.50, 0.24, new double[][] {{405,181},{562,208},{640,263}}, AMBER);
        pulseSegment(g, t, 3.76, 0.24, new double[][] {{565,315},{465,281}}, AMBER);
        pulseSegment(g, t, 4.02, 0.24, new double[][] {{565,435},{438,503}}, TEAL);
        double branches = active(t, 3.0, 1.35);
        if (branches >= 0) {
            if (branches < 0.37) {
                highlight(g, new Rectangle(168, 126, 153, 64), BLUE, 0.20);
            } else {
                highlight(g, new Rectangle(34, 285, 437, 84), AMBER, 0.16);
                if (branches > 0.76) highlight(g, new Rectangle(30, 440, 435, 132), TEAL, 0.12);
            }
        }

        // Document AI badges: subtle sequential checks, no movement or resizing.
        double ai = active(t, 4.5, 1.0);
        if (ai >= 0) {
            int n = Math.min(4, (int) (ai * 5));
            Rectangle[] badges = {
                new Rectangle(34, 458, 130, 34), new Rectangle(168, 458, 113, 34),
                new Rectangle(285, 458, 90, 34), new Rectangle(135, 497, 109, 34),
                new Rectangle(250, 497, 122, 34)
            };
            highlight(g, badges[n], TEAL, 0.28);
        }

        // Canonical schema and normalization groups receive a short processing sweep.
        double extraction = active(t, 5.5, 1.0);
        if (extraction >= 0) {
            int n = Math.min(5, (int) (extraction * 6));
            Rectangle[] groups = {
                new Rectangle(420, 614, 123, 29), new Rectangle(398, 645, 189, 31),
                new Rectangle(348, 676, 130, 29), new Rectangle(482, 676, 76, 29),
                new Rectangle(404, 705, 154, 29), new Rectangle(630, 696, 151, 28)
            };
            highlight(g, groups[n], TEAL, 0.26);
        }

        // Validation checks are lit one at a time; result tags are highlighted at phase end.
        double validation = active(t, 6.5, 1.0);
        if (validation >= 0) {
            int n = Math.min(4, (int) (validation * 5));
            Rectangle[] checks = {
                new Rectangle(922, 438, 146, 34), new Rectangle(1072, 438, 125, 34),
                new Rectangle(1198, 438, 129, 34), new Rectangle(948, 477, 145, 34),
                new Rectangle(1098, 477, 211, 34)
            };
            highlight(g, checks[n], n == 4 ? AMBER : TEAL, 0.45);
            if (validation > 0.87) highlight(g, new Rectangle(1175, 517, 138, 22), AMBER, 0.55);
        }

        // Low-confidence records change to a warm pulse on the existing review spoke.
        double review = active(t, 7.5, 1.0);
        if (review >= 0) {
            pulse(g, review, 0.8, new double[][] {{840,487},{866,538},{934,574}}, REVIEW);
            if (review > 0.78) highlight(g, new Rectangle(825, 565, 250, 180), REVIEW, 0.20);
        }

        // Distinct record and source-evidence pulses into their existing storage areas.
        double storage = active(t, 8.5, 0.8);
        if (storage >= 0) {
            pulse(g, storage, 0.8, new double[][] {{839,302},{958,270},{1060,249}}, BLUE);
            pulse(g, storage, 0.8, new double[][] {{841,318},{967,318},{1069,323}}, AMBER);
            highlight(g, new Rectangle(1000, 211, 372, 166), storage < 0.5 ? BLUE : AMBER, 0.18);
        }

        // Export badges highlight in sequence and remain in their original positions.
        double export = active(t, 9.5, 0.9);
        if (export >= 0) {
            int n = Math.min(2, (int) (export * 3));
            Rectangle[] badges = {new Rectangle(828, 678, 66, 56), new Rectangle(899, 678, 67, 56), new Rectangle(970, 678, 70, 56)};
            highlight(g, badges[n], TEAL, 0.42);
        }

        // Low-rate monitoring pulse on the existing operations connector.
        double ops = active(t, 10.15, 0.65);
        if (ops >= 0) {
            pulse(g, ops, 0.65, new double[][] {{846,495},{1010,548},{1124,594}}, new Color(151, 169, 215));
            highlight(g, new Rectangle(1090, 573, 292, 165), new Color(151, 169, 215), 0.12);
        }

        // Background queue heartbeat is deliberately faint and separate from invoice flow.
        double queue = active(t, 8.0, 0.45);
        if (queue >= 0) pulse(g, queue, 0.45, new double[][] {{1071,350},{1170,350},{1268,350}}, new Color(194, 145, 220));
    }

    private static void drawArchitecture(Graphics2D g, double t) {
        // Keep the detailed vertical workflow and its screenshot UI untouched; add only
        // moving dots along its existing connectors and transient component outlines.
        double input = active(t, 1.0, 1.0);
        if (input >= 0) {
            int n = Math.min(5, (int) (input * 6));
            Rectangle[] badges = {
                new Rectangle(181, 112, 44, 52), new Rectangle(278, 112, 43, 52),
                new Rectangle(366, 108, 43, 56), new Rectangle(449, 111, 42, 52),
                new Rectangle(494, 111, 43, 52), new Rectangle(585, 111, 44, 52)
            };
            highlight(g, badges[n], BLUE, 0.38);
        }
        pulseSegment(g, t, 2.0, 0.38, new double[][] {{384,190},{384,205},{384,319}}, BLUE);
        pulseSegment(g, t, 2.42, 0.36, new double[][] {{384,377},{384,391},{384,459}}, BLUE);

        double branches = active(t, 3.0, 1.5);
        if (branches >= 0) {
            if (branches < 0.5) {
                pulse(g, branches * 2, 0.7, new double[][] {{382,462},{208,481},{208,650},{385,675}}, BLUE);
                highlight(g, new Rectangle(39, 483, 338, 170), BLUE, 0.14);
            } else {
                pulse(g, (branches - 0.5) * 2, 0.7, new double[][] {{385,462},{560,481},{560,650},{385,675}}, AMBER);
                highlight(g, new Rectangle(392, 483, 337, 170), AMBER, 0.14);
            }
        }
        pulseSegment(g, t, 4.5, 0.46, new double[][] {{385,675},{385,789}}, TEAL);
        pulseSegment(g, t, 5.0, 0.44, new double[][] {{385,915},{385,928}}, TEAL);
        pulseSegment(g, t, 5.48, 0.38, new double[][] {{385,1044},{385,1060}}, TEAL);

        double checks = active(t, 6.5, 1.0);
        if (checks >= 0) {
            int n = Math.min(2, (int) (checks * 3));
            Rectangle[] boxes = {new Rectangle(202, 821, 165, 45), new Rectangle(369, 821, 199, 45), new Rectangle(218, 949, 164, 47)};
            highlight(g, boxes[n], n == 2 ? AMBER : TEAL, 0.35);
        }
        double review = active(t, 7.5, 1.0);
        if (review >= 0) {
            pulse(g, review, 0.85, new double[][] {{576,1043},{638,1043},{638,1064}}, REVIEW);
            if (review > 0.7) {
                highlight(g, new Rectangle(492, 1064, 237, 174), REVIEW, 0.14);
                int n = Math.min(3, (int) ((review - 0.7) * 13));
                Rectangle[] ui = {new Rectangle(516,1087,197,28),new Rectangle(514,1121,203,40),new Rectangle(512,1166,207,45),new Rectangle(512,1210,207,25)};
                highlight(g, ui[n], REVIEW, 0.38);
            }
        }
        double storage = active(t, 8.5, 0.85);
        if (storage >= 0) {
            pulse(g, storage, 0.85, new double[][] {{388,1077},{302,1087},{211,1110}}, BLUE);
            pulse(g, storage, 0.85, new double[][] {{388,1077},{390,1110},{390,1180}}, AMBER);
            highlight(g, new Rectangle(39, 1063, 368, 340), storage < 0.5 ? BLUE : AMBER, 0.10);
        }
        double export = active(t, 9.5, 0.9);
        if (export >= 0) {
            int n = Math.min(2, (int) (export * 3));
            Rectangle[] outputs = {new Rectangle(576,1335,49,51),new Rectangle(628,1335,49,51),new Rectangle(680,1335,49,51)};
            highlight(g, outputs[n], TEAL, 0.42);
            pulse(g, export, 0.65, new double[][] {{614,1235},{614,1270},{614,1322}}, TEAL);
        }
    }

    private static void highlight(Graphics2D g, Rectangle r, Color color, double strength) {
        Color fill = withAlpha(color, (float) strength);
        g.setColor(fill);
        g.fillRoundRect(r.x, r.y, r.width, r.height, 10, 10);
        g.setColor(withAlpha(color, (float) Math.min(0.72, strength + 0.22)));
        g.setStroke(new BasicStroke(1.5f));
        g.drawRoundRect(r.x, r.y, r.width, r.height, 10, 10);
    }

    private static void pulseSegment(Graphics2D g, double t, double start, double duration, double[][] points, Color c) {
        double p = active(t, start, duration);
        if (p >= 0) pulse(g, p, duration, points, c);
    }

    private static void pulse(Graphics2D g, double p, double ignored, double[][] points, Color c) {
        Point2D.Double at = along(points, Math.max(0, Math.min(1, p)));
        g.setColor(new Color(255, 255, 255, 210));
        g.fill(new Ellipse2D.Double(at.x - 6.2, at.y - 6.2, 12.4, 12.4));
        g.setColor(withAlpha(c, 0.88f));
        g.fill(new Ellipse2D.Double(at.x - 3.8, at.y - 3.8, 7.6, 7.6));
    }

    private static Point2D.Double along(double[][] points, double p) {
        double total = 0;
        double[] lengths = new double[points.length - 1];
        for (int i = 0; i < lengths.length; i++) {
            lengths[i] = Point2D.distance(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1]);
            total += lengths[i];
        }
        double target = p * total;
        for (int i = 0; i < lengths.length; i++) {
            if (target <= lengths[i] || i == lengths.length - 1) {
                double q = lengths[i] == 0 ? 0 : target / lengths[i];
                return new Point2D.Double(points[i][0] + (points[i + 1][0] - points[i][0]) * q,
                        points[i][1] + (points[i + 1][1] - points[i][1]) * q);
            }
            target -= lengths[i];
        }
        return new Point2D.Double(points[0][0], points[0][1]);
    }

    private static double active(double t, double start, double duration) {
        double p = (t - start) / duration;
        return p < 0 || p > 1 ? -1 : p;
    }

    private static Color withAlpha(Color c, float alpha) {
        return new Color(c.getRed(), c.getGreen(), c.getBlue(), Math.max(0, Math.min(255, Math.round(alpha * 255))));
    }

    private static void writeSvgSource(Path png, Path svg) throws Exception {
        // Editable SVG overlay source; the original PNG remains the unchanged background.
        String content = """
            <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1408" height="768" viewBox="0 0 1408 768">
              <image x="0" y="0" width="1408" height="768" href="../TechnologyStack.png" xlink:href="../TechnologyStack.png"/>
              <circle cx="704" cy="384" r="145" fill="none" stroke="#53cdc2" stroke-width="2" opacity=".24">
                <animate attributeName="r" values="145;148.5;145" dur="1.6s" repeatCount="indefinite"/>
                <animate attributeName="opacity" values=".16;.34;.16" dur="1.6s" repeatCount="indefinite"/>
              </circle>
              <circle r="4" fill="#6fabeB"><animateMotion dur="2.8s" begin="2s" repeatCount="indefinite" path="M704 239 L704 185 L704 239 L840 278 L886 240"/></circle>
              <circle r="4" fill="#53cdc2"><animateMotion dur="3.2s" begin="3s" repeatCount="indefinite" path="M565 315 L465 281 M565 435 L438 503 M696 529 L696 560"/></circle>
              <circle r="4" fill="#f48b73"><animateMotion dur="1s" begin="7.5s" repeatCount="indefinite" path="M840 487 L866 538 L934 574"/></circle>
              <g fill="none" stroke="#53cdc2" stroke-width="2" opacity=".7">
                <rect x="34" y="493" width="134" height="34" rx="7"><animate attributeName="opacity" values=".1;.85;.1" dur="5.5s" begin="4.5s" repeatCount="indefinite"/></rect>
                <rect x="168" y="493" width="128" height="34" rx="7"><animate attributeName="opacity" values=".1;.85;.1" dur="5.5s" begin="4.7s" repeatCount="indefinite"/></rect>
                <rect x="294" y="493" width="119" height="34" rx="7"><animate attributeName="opacity" values=".1;.85;.1" dur="5.5s" begin="4.9s" repeatCount="indefinite"/></rect>
                <rect x="135" y="530" width="116" height="34" rx="7"><animate attributeName="opacity" values=".1;.85;.1" dur="5.5s" begin="5.1s" repeatCount="indefinite"/></rect>
                <rect x="250" y="530" width="128" height="34" rx="7"><animate attributeName="opacity" values=".1;.85;.1" dur="5.5s" begin="5.3s" repeatCount="indefinite"/></rect>
              </g>
            </svg>
            """;
        Files.writeString(svg, content);
    }
}
