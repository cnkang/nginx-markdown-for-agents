#!/bin/bash
# Script to generate a huge HTML file (10MB+) for testing size limits

OUTPUT_FILE="huge-file.html"

cat > "$OUTPUT_FILE" << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Huge File Test</title>
</head>
<body>
    <!-- Test Purpose: Verify handling of 10MB+ HTML file and size limit enforcement -->
    <h1>Huge HTML File</h1>
    <p>This file is generated to be over 10MB in size to test resource limits.</p>
EOF

# Generate approximately 10MB of content
# Each iteration adds about 200 bytes, so we need about 50,000 iterations
for i in {1..50000}; do
    cat >> "$OUTPUT_FILE" << EOF
    <section id="section-$i">
        <h2>Section $i</h2>
        <p>This is paragraph $i with some content to increase file size. Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>
        <ul>
            <li>Item 1 in section $i</li>
            <li>Item 2 in section $i</li>
        </ul>
    </section>
EOF
done

cat >> "$OUTPUT_FILE" << 'EOF'
</body>
</html>
EOF

echo "Generated $OUTPUT_FILE"
ls -lh "$OUTPUT_FILE"
