from ok.feature.FeatureSet import compress_copy_coco

# from ok.util.coco import compress_and_copy_coco

compress_copy_coco('x-anylabeling-asset/annotations/coco_detection.json', 'assets',
                   image_folder='x-anylabeling-asset/project_dir')
