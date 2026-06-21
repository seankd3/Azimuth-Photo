package app.photoarchive.mobile;

import android.content.Context;
import android.graphics.Matrix;
import android.graphics.PointF;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;
import android.view.GestureDetector;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.view.VelocityTracker;
import android.widget.ImageView;

final class ZoomableImageView extends ImageView {

    interface SwipeListener {
        void onSwipeLeft();
        void onSwipeRight();
        void onSwipeDown();
    }

    private static final float MIN_SCALE = 1.0f;
    private static final float MAX_SCALE = 5.0f;
    private static final float DOUBLE_TAP_SCALE = 2.5f;
    private static final float SWIPE_VELOCITY_THRESHOLD = 1000f;
    private static final float SWIPE_DOWN_VELOCITY_THRESHOLD = 800f;

    private final Matrix imageMatrix = new Matrix();
    private final Matrix baseMatrix = new Matrix();
    private final float[] matrixValues = new float[9];
    private final RectF displayRect = new RectF();

    private final ScaleGestureDetector scaleDetector;
    private final GestureDetector gestureDetector;

    private Runnable onTapListener;
    private SwipeListener swipeListener;

    private boolean ready = false;
    private int viewWidth;
    private int viewHeight;

    ZoomableImageView(Context context) {
        super(context);
        setScaleType(ScaleType.MATRIX);

        scaleDetector = new ScaleGestureDetector(context, new ScaleGestureDetector.SimpleOnScaleGestureListener() {
            private float focusX;
            private float focusY;

            @Override
            public boolean onScaleBegin(ScaleGestureDetector detector) {
                focusX = detector.getFocusX();
                focusY = detector.getFocusY();
                return true;
            }

            @Override
            public boolean onScale(ScaleGestureDetector detector) {
                float factor = detector.getScaleFactor();
                float current = currentScale();
                float target = current * factor;
                if (target > MAX_SCALE) factor = MAX_SCALE / current;
                if (target < MIN_SCALE) factor = MIN_SCALE / current;
                imageMatrix.postScale(factor, factor, detector.getFocusX(), detector.getFocusY());
                constrainBounds();
                applyMatrix();
                return true;
            }
        });

        gestureDetector = new GestureDetector(context, new GestureDetector.SimpleOnGestureListener() {
            @Override
            public boolean onSingleTapConfirmed(MotionEvent e) {
                if (onTapListener != null) onTapListener.run();
                return true;
            }

            @Override
            public boolean onDoubleTap(MotionEvent e) {
                if (isZoomed()) {
                    resetZoom();
                } else {
                    float targetScale = DOUBLE_TAP_SCALE;
                    float current = currentScale();
                    float factor = targetScale / current;
                    imageMatrix.postScale(factor, factor, e.getX(), e.getY());
                    constrainBounds();
                    applyMatrix();
                }
                return true;
            }

            @Override
            public boolean onScroll(MotionEvent e1, MotionEvent e2, float distanceX, float distanceY) {
                if (isZoomed()) {
                    imageMatrix.postTranslate(-distanceX, -distanceY);
                    constrainBounds();
                    applyMatrix();
                    return true;
                }
                return false;
            }
        });
    }

    void setOnTapListener(Runnable listener) {
        this.onTapListener = listener;
    }

    void setOnSwipeListener(SwipeListener listener) {
        this.swipeListener = listener;
    }

    boolean isZoomed() {
        return currentScale() > MIN_SCALE + 0.05f;
    }

    void resetZoom() {
        imageMatrix.set(baseMatrix);
        applyMatrix();
    }

    @Override
    protected void onSizeChanged(int w, int h, int oldW, int oldH) {
        super.onSizeChanged(w, h, oldW, oldH);
        viewWidth = w;
        viewHeight = h;
        recalculateBase();
    }

    @Override
    public void setImageDrawable(Drawable drawable) {
        super.setImageDrawable(drawable);
        recalculateBase();
    }

    private VelocityTracker velocityTracker;
    private boolean handlingSwipe = false;

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        scaleDetector.onTouchEvent(event);
        gestureDetector.onTouchEvent(event);

        int action = event.getActionMasked();

        if (!isZoomed() && swipeListener != null) {
            switch (action) {
                case MotionEvent.ACTION_DOWN:
                    if (velocityTracker != null) velocityTracker.recycle();
                    velocityTracker = VelocityTracker.obtain();
                    velocityTracker.addMovement(event);
                    handlingSwipe = true;
                    break;
                case MotionEvent.ACTION_MOVE:
                    if (velocityTracker != null) velocityTracker.addMovement(event);
                    break;
                case MotionEvent.ACTION_UP:
                    if (velocityTracker != null && handlingSwipe) {
                        velocityTracker.addMovement(event);
                        velocityTracker.computeCurrentVelocity(1000);
                        float vx = velocityTracker.getXVelocity();
                        float vy = velocityTracker.getYVelocity();

                        if (Math.abs(vy) > SWIPE_DOWN_VELOCITY_THRESHOLD && vy > 0 && Math.abs(vy) > Math.abs(vx)) {
                            swipeListener.onSwipeDown();
                        } else if (Math.abs(vx) > SWIPE_VELOCITY_THRESHOLD) {
                            if (vx < 0) {
                                swipeListener.onSwipeLeft();
                            } else {
                                swipeListener.onSwipeRight();
                            }
                        }
                        velocityTracker.recycle();
                        velocityTracker = null;
                    }
                    handlingSwipe = false;
                    break;
                case MotionEvent.ACTION_CANCEL:
                    if (velocityTracker != null) {
                        velocityTracker.recycle();
                        velocityTracker = null;
                    }
                    handlingSwipe = false;
                    break;
            }
        } else if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) {
            if (velocityTracker != null) {
                velocityTracker.recycle();
                velocityTracker = null;
            }
            handlingSwipe = false;
        }

        return true;
    }

    private void recalculateBase() {
        Drawable drawable = getDrawable();
        if (drawable == null || viewWidth == 0 || viewHeight == 0) {
            ready = false;
            return;
        }
        ready = true;
        int drawableWidth = drawable.getIntrinsicWidth();
        int drawableHeight = drawable.getIntrinsicHeight();
        if (drawableWidth <= 0 || drawableHeight <= 0) return;

        float scaleX = (float) viewWidth / drawableWidth;
        float scaleY = (float) viewHeight / drawableHeight;
        float scale = Math.min(scaleX, scaleY);

        float dx = (viewWidth - drawableWidth * scale) / 2f;
        float dy = (viewHeight - drawableHeight * scale) / 2f;

        baseMatrix.reset();
        baseMatrix.postScale(scale, scale);
        baseMatrix.postTranslate(dx, dy);

        imageMatrix.set(baseMatrix);
        applyMatrix();
    }

    private float currentScale() {
        imageMatrix.getValues(matrixValues);
        return matrixValues[Matrix.MSCALE_X];
    }

    private void constrainBounds() {
        Drawable drawable = getDrawable();
        if (drawable == null || viewWidth == 0 || viewHeight == 0) return;

        int drawableWidth = drawable.getIntrinsicWidth();
        int drawableHeight = drawable.getIntrinsicHeight();
        if (drawableWidth <= 0 || drawableHeight <= 0) return;

        displayRect.set(0, 0, drawableWidth, drawableHeight);
        imageMatrix.mapRect(displayRect);

        float dx = 0, dy = 0;

        if (displayRect.width() <= viewWidth) {
            dx = (viewWidth - displayRect.width()) / 2f - displayRect.left;
        } else {
            if (displayRect.left > 0) dx = -displayRect.left;
            else if (displayRect.right < viewWidth) dx = viewWidth - displayRect.right;
        }

        if (displayRect.height() <= viewHeight) {
            dy = (viewHeight - displayRect.height()) / 2f - displayRect.top;
        } else {
            if (displayRect.top > 0) dy = -displayRect.top;
            else if (displayRect.bottom < viewHeight) dy = viewHeight - displayRect.bottom;
        }

        imageMatrix.postTranslate(dx, dy);
    }

    private void applyMatrix() {
        setImageMatrix(imageMatrix);
    }
}
